"""Tavily discovery produces leads, never purchase evidence or automatic admission."""
from dataclasses import dataclass, field
from decimal import Decimal
import math
import time
from urllib.parse import urlsplit
import httpx2

from sqlalchemy import select

from app.accounting import BudgetDenied, Charge, money
from app.contracts_v2 import ShoppingRequest, CoverageCounts, VerifiedOffer
from app.directory import DirectoryRecord
from app.provider_http import post_json
from app.source_capabilities import CapabilityRef, safe_endpoint
from app.sources import FetchOutcome
from app.storage import directory_entries


@dataclass(frozen=True)
class Lead:
    url: str
    title: str
    snippet: str
    relevance: float


def search_query(request: ShoppingRequest):
    intent = request.intent
    subject = (' '.join(v for v in (intent.manufacturer, intent.model) if v) if intent.kind == 'exact_model'
               else str(intent.url) if intent.kind == 'product_link' else intent.description)
    requirements = request.requirements.model_dump(exclude_none=True)
    parts = [subject, *[f'{key} {value}' for key,value in requirements.items()],
             'condition ' + ' '.join(request.allowed_conditions)]
    if request.budget:
        for label, value in (('maximum',request.budget.maximum),('target',request.budget.target)):
            if value:
                parts.append(f'{label} {value.amount} {value.currency}')
    parts.append('Lebanon')
    if request.market_scope.include_international:
        parts.append('international ' + ' '.join(request.market_scope.international_markets))
    query = ' '.join(parts)
    if len(query) > 1800:
        raise ValueError('Search scope is too large; clarify instead of dropping buyer constraints')
    return query


class Tavily:
    def __init__(self, api_key, *, usd_per_credit: Decimal, transport=None):
        if money(usd_per_credit) == 0 or not api_key.strip():
            raise ValueError('Explicit key and positive configured credit rate are required')
        self.api_key, self.rate, self.transport = api_key, usd_per_credit, transport

    def search(self, request: ShoppingRequest, job, *, max_results=20):
        if type(max_results) is not int or not 1 <= max_results <= 20:
            raise ValueError('One bounded search returns 1–20 leads')
        payload = dict(query=search_query(request), search_depth='basic', topic='general',
            max_results=max_results, auto_parameters=False, include_answer=False,
            include_raw_content=False, include_images=False, include_usage=True)
        if not request.market_scope.include_international:
            payload['country'] = 'lebanon'  # ranking hint, not proof of seller market
        attempt = job.reserve('search', Charge(self.rate), 20,
                              international=request.market_scope.include_international)
        started = time.monotonic()
        attempt.dispatch()
        try:
            raw = post_json('https://api.tavily.com/search', self.api_key, payload, transport=self.transport)
            rows = raw['results']
            if not isinstance(rows,list) or len(rows) > max_results:
                raise ValueError('Invalid search result bound')
            leads = []
            for row in rows:
                url, title, snippet, score = (row.get(k) for k in ('url','title','content','score'))
                if (not all(isinstance(v,str) for v in (url,title,snippet)) or len(url)>2048
                    or len(title)>1000 or len(snippet)>16000 or type(score) not in (int,float)
                    or not math.isfinite(score) or not 0 <= score <= 1):
                    raise ValueError('Malformed search result')
                host = urlsplit(url).hostname
                if host and safe_endpoint(url,host):
                    leads.append(Lead(url,title,snippet,float(score)))
            credits = raw.get('usage',{}).get('credits')
            if type(credits) is int and credits > 1:
                attempt.complete(self.rate * credits, time.monotonic()-started)
            if type(credits) is not int or credits != 1:
                raise ValueError('Basic-search usage is missing or exceeds the quote')
        except Exception:
            try:
                attempt.uncertain()
            except BudgetDenied:
                job.ledger.recover()
            raise
        attempt.complete(self.rate, time.monotonic()-started)
        return tuple(leads)


@dataclass
class RetailerProgress:
    retailer_id: str
    source_id: str
    market: str
    lead: Lead
    status: str = 'unchecked'
    reason: str = 'not selected'
    outcome: FetchOutcome | None = None
    qualifying: bool = False


@dataclass
class DiscoveryReport:
    run_id: str
    leads: tuple[Lead,...]
    retailers: dict[str,RetailerProgress] = field(default_factory=dict)
    search_error: str | None = None

    @property
    def coverage(self):
        rows = list(self.retailers.values())
        return CoverageCounts(discovered=len(rows), attempted=sum(r.status in {'failed','checked'} for r in rows),
            retrieved=sum(r.status=='checked' for r in rows), qualifying=sum(r.qualifying for r in rows))

    def qualify(self, verified: VerifiedOffer):
        # Only downstream current-run verification can advance this coverage level.
        verified = VerifiedOffer.model_validate(verified.model_dump(mode='json'))
        row = self.retailers[verified.offer.retailer_id]
        if row.status != 'checked' or verified.verification.run.run_id != self.run_id:
            raise ValueError('Qualification requires a retrieved retailer in this run')
        evidence = {e.evidence_id:e for e in verified.offer.evidence}
        for eid in (*verified.offer.price.evidence_ids,*verified.offer.stock.evidence_ids):
            receipt = evidence[eid]
            if receipt.source_id != row.source_id or receipt.response_sha256 != row.outcome.response_sha256:
                raise ValueError('Qualification must cite this direct retailer retrieval')
        row.qualifying = True


class Discovery:
    def __init__(self, provider, directory, gate):
        self.provider, self.directory, self.gate = provider, directory, gate

    def _records(self, leads):
        # Match only reviewed canonical origins. No global 15-store shortlist:
        # an arbitrary newly registered retailer can be returned by this search.
        origins = {f'https://{urlsplit(l.url).netloc}' for l in leads}
        with self.directory.storage.engine.connect() as db:
            rows = db.execute(select(directory_entries.c.payload).where(
                directory_entries.c.payload['entry']['origin'].as_string().in_(
                    origins | {origin+'/' for origin in origins}))).scalars().all()
        return tuple(DirectoryRecord.model_validate(row) for row in rows)

    def discover(self, request: ShoppingRequest, job, *, max_sources=3, min_relevance=.25,
                 already_covered=()):
        if type(max_sources) is not int or not 1 <= max_sources <= 20 or not 0 <= min_relevance <= 1:
            raise ValueError('Invalid source-selection bounds')
        try:
            leads = self.provider.search(request,job,max_results=20)
        except (BudgetDenied, ValueError, KeyError, TimeoutError, httpx2.HTTPError) as exc:
            return DiscoveryReport(job.run_id, (), search_error=type(exc).__name__)
        # Provider transports may raise their own network errors. They remain
        # failures, never empty successful searches.
        report = DiscoveryReport(job.run_id, leads)
        records = self._records(leads)
        candidates = []
        covered = set(already_covered)
        for record in records:
            entry = record.entry
            if record.seller_identity != 'retailer' or not record.source_id or entry.discovery_status == 'retired':
                continue  # aggregators/marketplaces/unresolved domains remain leads
            relevant = [l for l in leads if urlsplit(l.url).hostname == entry.origin.host]
            if not relevant:
                continue
            lead = max(relevant,key=lambda l:l.relevance)
            market = record.source_country
            row = RetailerProgress(entry.retailer_id,record.source_id,market or '',lead)
            report.retailers[entry.retailer_id] = row
            if market is None or not request.market_scope.allows(market):
                row.reason = 'market not opted in or unresolved'
                continue
            if request.requirements.category not in entry.categories or lead.relevance < min_relevance:
                row.reason = 'insufficient relevance or category coverage'
                continue
            if entry.retailer_id in covered:
                row.reason = 'already covered; no marginal seller comparison'
                continue
            capability = None
            expect = 'html'
            # Direct source gate admission, including release selection and
            # current capability versions, is authoritative before retrieval.
            try:
                policy = self.gate.admitted_policy(record.source_id)
                if lead.url not in {str(url) for url in policy.allowed_endpoints}:
                    matching = [c for c in policy.endpoint_capabilities if c.matches(lead.url)]
                    if not matching:
                        raise ValueError('No listing capability')
                    rule = matching[0]
                    capability, expect = CapabilityRef(capability_id=rule.capability_id,version=rule.version),rule.expect
                self.gate.check_scope(record.source_id,lead.url,expect=expect,capability=capability)
            except (ValueError, LookupError):
                row.reason = 'source needs technical configuration'
                continue
            untested = any(c.category==request.requirements.category and not c.retrieval_tested for c in record.coverage)
            candidates.append((lead.relevance + (.05 if untested else 0),row,capability,expect))
        candidates.sort(key=lambda c:(-c[0],c[1].retailer_id))
        for _,row,capability,expect in candidates[:max_sources]:
            outcome = self.gate.fetch(job.source_budget(self.directory.registry_path,international=row.market!='LB'),
                row.source_id,row.lead.url,expect=expect,capability=capability)
            row.outcome = outcome
            row.status = 'checked' if outcome.status == 'ok' else 'failed' if outcome.attempts else 'unchecked'
            row.reason = 'direct listing retrieved; verification required' if outcome.status=='ok' else 'retrieval refused or failed'
        return report
