"""Shared retrieval/extraction entry point for buyer and collection workers."""
from dataclasses import dataclass
from decimal import Decimal
import time

from app.accounting import Charge, BudgetDenied
from app.corpus_contracts import DocumentCapture, Extraction
from app.extraction import extract_page
from app.firecrawl import retrieve_page
from app.sources import FetchOutcome


@dataclass(frozen=True)
class PageResult:
    outcome: FetchOutcome
    capture: DocumentCapture | None
    extraction: Extraction | None
    method: str


def read_page(gate, job, key, profile, processing, *, origin, dedicated=None,
              capability=None, fallback=None, international=False, run=None):
    if run is not None and (run.run_id != job.run_id or origin != 'runtime'):
        raise ValueError('Runtime extraction must belong to the accounting job')
    class FallbackOnce:
        used = False
        def scrape(self, *args, **kwargs):
            self.used = True
            return fallback.scrape(*args, **kwargs)
    managed_fallback = FallbackOnce() if fallback is not None else None
    outcome = retrieve_page(gate,job,key.source_id,str(key.url),capability=capability,
                            fallback=managed_fallback,international=international)
    if outcome.status != 'ok':
        return PageResult(outcome,None,None,'failed')

    def process(received):
        capture = DocumentCapture(key=key,final_url=received.final_url,visibility='public',origin=origin,
            observed_at=received.observed_at,checked_at=received.observed_at,body=received.body,
            relevant_content=received.body,processing=processing)
        attempt = job.reserve('work',Charge(Decimal(0)),5,international=international)
        started = time.monotonic()
        attempt.dispatch()
        try:
            extraction = extract_page(capture,profile,dedicated=dedicated,run=run)
        except Exception:
            try:
                attempt.uncertain()
            except BudgetDenied:
                job.ledger.recover()
            raise
        attempt.complete(Decimal(0),time.monotonic()-started)
        return capture,extraction

    capture,extraction = process(outcome)
    method = 'dedicated' if dedicated else 'managed-shared' if managed_fallback and managed_fallback.used else 'shared'
    # An admitted HTML shell may need rendering even though the direct GET
    # succeeded. Only explicitly configured fallback can make that extra call.
    if not extraction.observations and dedicated is None and managed_fallback is not None and not managed_fallback.used:
        managed = managed_fallback.scrape(job,key.source_id,str(key.url),capability=capability,international=international)
        outcome = managed.outcome
        capture,extraction = process(outcome)
        method = 'managed-shared'
    return PageResult(outcome,capture,extraction,method)
