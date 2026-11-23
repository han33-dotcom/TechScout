"""Bounded OpenRouter passage and query embeddings, with durable accounting."""
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import os
import time
import uuid

import httpx2
from sqlalchemy import insert, select, update

from app.accounting import BudgetDenied, Charge, Ledger
from app.corpus import Corpus
from app.openrouter import _wait
from app.provider_http import post_json
from app.retrieval import DIMENSIONS, ROUTED_MODEL, checked_vector, routed_index_version
from app.storage import Storage, corpus_documents, embedding_calls, _now


ENDPOINT = 'https://openrouter.ai/api/v1/embeddings'
RATE_VARIABLE = 'TECHSCOUT_EMBEDDING_USD_PER_MILLION'


class EmbeddingAllowanceExceeded(ValueError):
    """The pending batch requires more than the caller's spend allowance."""


class EmbeddingUnavailable(ValueError):
    """Sanitized reason a routed vector could not be produced."""


class EmbeddingIndexBusy(EmbeddingUnavailable):
    """Another process owns a bounded batch for this resource and index."""


class OpenRouterEmbeddings:
    def __init__(self, api_key: str, *, transport=None):
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError('A backend OPENROUTER_API_KEY is required')
        self._key = api_key
        self._transport = transport

    def __call__(self, texts):
        payload = dict(model=ROUTED_MODEL, dimensions=DIMENSIONS, encoding_format='float',
                       input=list(texts), provider={'only': ['openai'], 'allow_fallbacks': False,
                                                    'data_collection': 'deny'})
        return post_json(ENDPOINT, self._key, payload, transport=self._transport, seconds=18,
                         max_bytes=4_000_000, headers={'X-OpenRouter-Metadata': 'enabled'},
                         parse_float=Decimal)


@dataclass(frozen=True)
class EmbeddingRoute:
    provider: object = None
    rate: Decimal | None = None
    unavailable: str | None = None

    @classmethod
    def configured(cls, *, rate=None, environ=None, transport=None):
        environ = os.environ if environ is None else environ
        key = environ.get('OPENROUTER_API_KEY', '').strip()
        if not key:
            return cls(unavailable='missing OPENROUTER_API_KEY')
        try:
            rate = Decimal(rate if rate is not None else environ.get(RATE_VARIABLE, ''))
            if not rate.is_finite() or rate <= 0:
                raise ValueError()
        except (InvalidOperation, ValueError):
            return cls(unavailable='positive all-inclusive embedding rate required')
        return cls(OpenRouterEmbeddings(key, transport=transport), rate)


def _identifier(value):
    return value[:160] if isinstance(value, str) else None


def _serving_endpoint(reply):
    metadata = reply.get('openrouter_metadata')
    endpoints = metadata.get('endpoints') if isinstance(metadata, dict) else None
    available = endpoints.get('available') if isinstance(endpoints, dict) else None
    if isinstance(available, list):
        for endpoint in available:
            if isinstance(endpoint, dict) and endpoint.get('selected') is True:
                return endpoint
    return {}


def _usage(reply, rate):
    usage = reply.get('usage')
    usage = usage if isinstance(usage, dict) else {}
    tokens = usage.get('total_tokens')
    if type(tokens) is not int or not 0 <= tokens <= 2_147_483_647:
        tokens = None
    cost = usage.get('cost')
    reported = (Decimal(cost) if type(cost) in (int, Decimal) and
                Decimal(cost).is_finite() and cost >= 0 else None)
    byok = usage.get('is_byok')
    details = dict(tokens=tokens, rate_estimate_usd=str(tokens*rate/1_000_000) if tokens is not None else None,
                   reported_cost_usd=str(reported) if reported is not None else None,
                   is_byok=byok if type(byok) is bool else None)
    return details, reported if byok is False else None


def _vectors(reply, count):
    if reply.get('error') or reply.get('model') != ROUTED_MODEL:
        raise EmbeddingUnavailable('refused or unexpected embedding model')
    rows = reply.get('data')
    if not isinstance(rows, list) or len(rows) != count:
        raise EmbeddingUnavailable('invalid embedding batch')
    try:
        if any(type(row['index']) is not int for row in rows):
            raise ValueError()
        rows = sorted(rows, key=lambda row: row['index'])
        if [row['index'] for row in rows] != list(range(count)):
            raise ValueError()
        return tuple(checked_vector(row['embedding']) for row in rows)
    except (KeyError, ValueError, TypeError, ArithmeticError):
        raise EmbeddingUnavailable('invalid embedding dimensions, values or indexes') from None


class EmbeddingService:
    def __init__(self, corpus: Corpus, provider, *, usd_per_million: Decimal, job=None, international=False):
        if (not isinstance(usd_per_million, Decimal) or not usd_per_million.is_finite()
                or usd_per_million <= 0):
            raise ValueError('Supply the configured positive Decimal embedding rate')
        self.corpus, self.provider, self.rate = corpus, provider, usd_per_million
        self.job, self.international = job, international
        if isinstance(provider, OpenRouterEmbeddings) and job is None:
            raise ValueError('Live embeddings require the central accounting job')

    def embed(self, texts, *, owner_id: str, purpose: str, index_version: str, max_spend_usd: Decimal):
        texts = tuple(texts)
        if (not 1 <= len(texts) <= 32 or any(not isinstance(t, str) or not t.strip()
                or len(t.encode('utf-8')) > 8000 for t in texts)):
            raise EmbeddingUnavailable('Embed 1–32 nonempty texts, each at most 8000 UTF-8 bytes')
        if not owner_id.strip() or not index_version.strip() or purpose not in {'query', 'index'}:
            raise ValueError('Embedding usage needs an owner, purpose and index version')
        if not isinstance(max_spend_usd, Decimal) or not max_spend_usd.is_finite() or max_spend_usd < 0:
            raise ValueError('Embedding spend allowance must be a nonnegative Decimal')
        reserved_tokens = sum(len(t.encode('utf-8')) for t in texts)
        reserved = Decimal(reserved_tokens) * self.rate / Decimal(1_000_000)
        if reserved > max_spend_usd:
            raise EmbeddingAllowanceExceeded('Embedding call exceeds its spend allowance')

        call_id = uuid.uuid4().hex
        version = routed_index_version(index_version)
        attempt = self.job.reserve('embedding', Charge(reserved), 20,
                                   international=self.international) if self.job else None
        sent = False
        started = time.monotonic()
        receipt = dict(call_id=call_id, endpoint=ENDPOINT, requested_model=ROUTED_MODEL,
                       requested_provider='openai', dimensions=DIMENSIONS, index_version=version,
                       purpose=purpose, reserved_usd=str(reserved))
        actual = tokens = None
        try:
            with self.corpus.storage.transaction() as db:
                db.execute(insert(embedding_calls).values(call_id=call_id, owner_id=owner_id,
                    purpose=purpose, model=ROUTED_MODEL, index_version=version, status='reserved',
                    reserved_usd=reserved, rate_usd_per_million=self.rate, recorded_at=_now()))
            if attempt:
                attempt.dispatch()
            sent = True
            kind, reply = _wait(lambda: self.provider(texts), started + 18,
                                lambda: bool(self.job and self.job.cancelled))
            if kind != 'reply':
                if isinstance(reply, httpx2.HTTPStatusError):
                    raise EmbeddingUnavailable(f'OpenRouter HTTP {reply.response.status_code}')
                raise EmbeddingUnavailable(f'OpenRouter {kind}')
            if not isinstance(reply, dict):
                raise EmbeddingUnavailable('invalid OpenRouter response')
            selected = _serving_endpoint(reply)
            receipt.update(reported_model=_identifier(reply.get('model')),
                           serving_provider=_identifier(reply.get('provider')) or _identifier(selected.get('provider')),
                           serving_endpoint=_identifier(selected.get('name') or selected.get('id')),
                           response_id=_identifier(reply.get('id')))
            usage, actual = _usage(reply, self.rate)
            tokens = usage['tokens']
            receipt.update(usage)
            vectors = _vectors(reply, len(texts))
            if receipt['serving_provider'] and receipt['serving_provider'].lower() != 'openai':
                raise EmbeddingUnavailable('unexpected serving provider')
            receipt['result'] = 'validated'
            return vectors
        except EmbeddingUnavailable as exc:
            receipt['result'] = str(exc)
            raise
        finally:
            elapsed = time.monotonic() - started
            status = 'completed' if actual is not None else 'uncertain' if sent else 'not_sent'
            receipt.update(latency_ms=round(elapsed*1000), billing=status,
                           actual_usd=str(actual) if actual is not None else None)
            try:
                if attempt:
                    attempt.record(receipt)
                    if not sent:
                        attempt.release()
                    elif actual is None:
                        attempt.uncertain()
                    else:
                        attempt.complete(actual, elapsed)
            except BudgetDenied:
                self.job.ledger.recover()
                # A late result cannot release or settle the held reservation. A
                # genuine billed overrun, however, is already posted as completed.
                settled = self.job.ledger.snapshot()['attempts'][attempt.token]
                status = 'completed' if settled['status'] == 'completed' else 'uncertain'
                if status == 'uncertain':
                    actual = None
                raise
            finally:
                with self.corpus.storage.transaction() as db:
                    db.execute(update(embedding_calls).where(embedding_calls.c.call_id == call_id)
                               .values(status=status, tokens=tokens, actual_usd=actual))

    @contextmanager
    def _index_lease(self, key):
        """Serialize pending checks for the same resource across bootstrap and refresh jobs."""
        if self.job is None:
            yield
            return
        token = uuid.uuid4().hex
        with self.job.ledger._transaction() as (state, now):
            leases = state.setdefault('embedding_index_leases', {})
            for expired in [k for k, lease in leases.items() if lease['until'] <= now]:
                del leases[expired]
            if key in leases:
                raise EmbeddingIndexBusy('another job is indexing this resource')
            leases[key] = dict(owner=token, until=now+120)
        try:
            yield
        finally:
            with self.job.ledger._transaction() as (state, _):
                leases = state['embedding_index_leases']
                if leases.get(key, {}).get('owner') == token:
                    del leases[key]

    def index_document(self, document_id: int, *, owner_id: str, max_spend_usd: Decimal):
        """Index one bounded batch of previously unindexed passages in the routed space."""
        with self.corpus.storage.engine.connect() as db:
            resource, recipe = db.execute(select(corpus_documents.c.resource_id, corpus_documents.c.processing)
                .where(corpus_documents.c.document_id == document_id)).one()
        index = recipe['index_version']
        version = routed_index_version(index)
        with self._index_lease(f'{resource}/{version}'):
            pending = set(self.corpus.pending_embeddings(document_id, ROUTED_MODEL, DIMENSIONS, index_version=version))
            if not pending:
                return 0
            selected = [p for p in self.corpus.passages(document_id) if int(p.passage_id) in pending][:32]
            vectors = self.embed([p.text for p in selected], owner_id=owner_id, purpose='index',
                                 index_version=index, max_spend_usd=max_spend_usd)
            for passage, vector in zip(selected, vectors):
                self.corpus.save_embedding(int(passage.passage_id), version, ROUTED_MODEL, list(vector))
            return len(selected)


def check_route(route, ledger, storage):
    """One explicit, budgeted request with public input; return only receipt metadata."""
    if route.unavailable:
        raise EmbeddingUnavailable(route.unavailable)
    job = ledger.open_job('embedding-check:' + uuid.uuid4().hex, 'bootstrap')
    service = EmbeddingService(Corpus(storage), route.provider, usd_per_million=route.rate, job=job)
    try:
        service.embed(['TechScout'], owner_id=job.run_id, purpose='query', index_version='route-check',
                      max_spend_usd=Decimal('0.000001'))
    except EmbeddingUnavailable:
        # The sanitized failure receipt is the evidence for #80.
        pass
    attempts = ledger.snapshot()['attempts'].values()
    attempt, = (a for a in attempts if a['job'] == job.run_id)
    return dict(job=job.run_id, status=attempt['status'], **attempt.get('provider_usage', {}))


def main():
    import argparse
    from app.inference import load_env_file
    parser = argparse.ArgumentParser(description='Check the issued OpenRouter embedding route once.')
    parser.add_argument('--check-route', action='store_true', required=True)
    parser.add_argument('--embedding-rate', type=Decimal, help='All-inclusive USD per million input tokens')
    args = parser.parse_args()
    load_env_file(names=('OPENROUTER_API_KEY', RATE_VARIABLE))
    route = EmbeddingRoute.configured(rate=args.embedding_rate)
    if route.unavailable:
        parser.exit(2, route.unavailable + '\n')
    ledger, storage = Ledger.configured(), Storage()
    try:
        receipt = check_route(route, ledger, storage)
        print(json.dumps(receipt, indent=2))
        return int(receipt.get('result') != 'validated')
    finally:
        storage.engine.dispose()
        ledger.storage.engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
