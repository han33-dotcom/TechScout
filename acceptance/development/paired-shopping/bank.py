"""Local, immutable per-key development responses. Never publish the raw bank."""
import base64
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit, urlunsplit

from app.accounting import BudgetDenied, Charge
from app.contracts import ToolError
from app.domestic_discovery import CHANNEL_LIMITS, ChannelAttempt, DomesticDiscovery
from app.search_connectors import Hit
from app.sources import FetchOutcome


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), default=str).encode()).hexdigest()


class Bank:
    def __init__(self, path, mode, events):
        self.path, self.mode, self.events = Path(path), mode, Path(events)
        if mode not in ('live', 'replay'):
            raise ValueError('Expected live or replay')
        self.lock = Lock()
        if mode == 'live':
            self.path.mkdir(parents=True, exist_ok=True)
        elif not self.path.is_dir():
            raise ValueError('Replay requires a captured bank')

    def event(self, kind, **data):
        with self.lock, self.events.open('a', encoding='utf-8') as out:
            print(json.dumps(dict(at=datetime.now(timezone.utc).isoformat(), kind=kind,
                                  mode=self.mode, **data), default=str), file=out)

    def exchange(self, kind, key, capture):
        path = self.path / (digest([kind, key]) + '.json')
        if self.mode == 'replay':
            if path.is_file():
                return json.loads(path.read_bytes())['response']
            self.event('replay_miss', operation=kind, key=key)
            return None
        result = capture()
        # Retain the first capture for a scoped request; live callers always receive
        # their actual response, including changes observed after the bank was frozen.
        with self.lock:
            if not path.exists():
                tmp = path.with_suffix('.tmp')
                tmp.write_text(json.dumps(dict(operation=kind, key=key, response=result),
                                          default=str), encoding='utf-8')
                tmp.replace(path)
        return result


class SharedSearch(DomesticDiscovery):
    def __init__(self, original, bank):
        super().__init__(original.gate, original.directory, original.connectors, fallback=original.fallback)
        self.bank = bank

    def _attempt(self, search, job, cursor):
        key = dict(provider=search.channel, query=search.query, country='LB',
                   language=search.language, limit=search.limit, cursor=cursor)

        def capture():
            attempt, hits = super(SharedSearch, self)._attempt(search, job, cursor)
            return dict(attempt=asdict(attempt), hits=[asdict(h) for h in hits])

        result = self.bank.exchange('search', key, capture)
        if result is None:
            return ChannelAttempt(search.channel, 'unavailable'), ()
        data = dict(result['attempt'])
        if self.bank.mode == 'replay' and data['status'] not in ('unavailable', 'budget_denied', 'cancelled'):
            try:
                if search.channel == 'manufacturer':
                    connector = self.connectors[search.channel]
                    pages = [p for p in connector.pages if p.brand.casefold() in search.query.casefold()] or connector.pages
                    page = pages[cursor % len(pages)]
                    account_read(job.source_budget(self.directory.registry_path), page.source_id, page.url)
                else:
                    attempt = job.reserve('search', Charge(Decimal(0)), 1,
                                          source='discovery:' + search.channel, limits=CHANNEL_LIMITS)
                    attempt.dispatch()
                    attempt.complete(Decimal(0), 0)
            except BudgetDenied:
                return ChannelAttempt(search.channel, 'budget_denied'), ()
        for field in ('reserved_usd', 'actual_usd'):
            if data[field] is not None:
                data[field] = Decimal(data[field])
        self.bank.event('search', **key, status=data['status'], count=len(result['hits']),
                        urls=[urlunsplit((*urlsplit(h['url'])[:3], '', '')) for h in result['hits']],
                        response=digest(result))
        return ChannelAttempt(**data), tuple(Hit(**h) for h in result['hits'])


class SharedGate:
    """Keep admission and source accounting at their original transport boundary."""
    def __init__(self, gate, bank):
        self.original, self.bank = gate, bank
        self.documents = {}
        self.lock = Lock()

    def __getattr__(self, key):
        return getattr(self.original, key)

    def fetch(self, budget, source_id, url, *, expect, capability=None, validators=None):
        key = dict(source=source_id, url=url, expect=expect,
                   capability=capability.model_dump() if capability else None)
        if validators:
            raise ValueError('Conditional retrieval is outside this development profile')

        def capture():
            return pack(self.original.fetch(budget, source_id, url, expect=expect, capability=capability))

        if self.bank.mode == 'replay':
            self.original.check_scope(source_id, url, expect=expect, capability=capability)
        result = self.bank.exchange('get', key, capture)
        if self.bank.mode == 'replay' and result:
            for _ in range(result['attempts']):
                account_read(budget, source_id, url)
        outcome = unpack(result, source_id, url, self.bank.mode)
        if expect == 'html':
            self.remember(url, outcome)
        return outcome

    def remember(self, url, outcome):
        with self.lock:
            if outcome.status == 'ok' and outcome.final_url == url:
                self.documents[url] = outcome
            else:
                self.documents.pop(url, None)


def account_read(budget, source, url):
    lease = budget.reserve_resource(source, url, 1)
    if lease is None or not lease.still_wanted():
        if lease:
            lease.release()
        raise BudgetDenied('Replay source allowance exhausted')
    lease.commit(0)


def pack(outcome):
    data = {k: v for k, v in vars(outcome).items() if k not in ('validators', 'errors', 'body')}
    return dict(**data, errors=[e.model_dump(mode='json') for e in outcome.errors],
                body=base64.b64encode(outcome.body).decode() if outcome.body is not None else None)


def unpack(data, source, url, mode):
    if data is None:
        return FetchOutcome(source, url, (url,), 0, 0, errors=(ToolError(
            code='BLOCKED', message='Uncaptured replay request', retryable=False),))
    data = dict(data)
    data['errors'] = tuple(ToolError.model_validate(e) for e in data['errors'])
    data['redirect_chain'] = tuple(data['redirect_chain'])
    if data['body'] is not None:
        data['body'] = base64.b64decode(data['body'])
    if data['observed_at']:
        # Re-delivery to this isolated run has its own clock. Capture time remains in the bank.
        data['observed_at'] = (datetime.now(timezone.utc) if mode == 'replay' else
                               datetime.fromisoformat(str(data['observed_at'])))
    return FetchOutcome(**data)


class SharedScrape:
    def __init__(self, fallback, bank, gate):
        self.fallback, self.bank, self.gate = fallback, bank, gate

    def scrape(self, job, source_id, url, *, capability=None, international=False):
        from app.firecrawl import ManagedPage
        key = dict(source=source_id, url=url, capability=capability.model_dump() if capability else None,
                   international=international)

        def capture():
            reply = self.fallback.scrape(job, source_id, url, capability=capability, international=international)
            return pack(reply.outcome)

        self.gate.check_scope(source_id, url, expect='html', capability=capability)
        result = self.bank.exchange('scrape', key, capture)
        if self.bank.mode == 'replay' and result:
            account_read(job.source_budget(self.gate._registry_path, international=international), source_id, url)
        outcome = unpack(result, source_id, url, self.bank.mode)
        self.gate.remember(url, outcome)
        self.bank.event('scrape', source=source_id, url=url, status=outcome.status)
        return ManagedPage(outcome, 'firecrawl-v2', None)
