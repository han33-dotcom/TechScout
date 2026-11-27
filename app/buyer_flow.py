"""Trusted buyer controller for the first live browser-to-result flow (#75, T39).

Start it as the buyer worker's controller, in its own backend process:

    python -m app.run_worker --controller app.buyer_flow:run

The normal worker uses the catalog-first domestic controller in app.domestic_buyer.
The following checkpoint remains available as app.buyer_flow:run_checkpoint.

Checkpoint scope: one source (Class Cellulars) and one product type (an exact-model phone search,
delivered in Lebanon). Other requests end as an honest abstention that says so.

The run goes: interpret the request; read the listings (live, or listings stored by an
earlier run); let the orchestrator plan research over the listings the research workers can
actually read (the workers and the evidence critique run inside it); assess eligibility and
cost with the fixed rules; enter the final phase and re-read the finalists live; write the
explanation; publish. Models only choose: which listings to investigate, which resource to
read next, which evidence issue deserves one follow-up, and which checked claims to show.
Code decides eligibility, cost and verification, and only a finalist whose price and stock
come from the final re-read can be recommended (app.verification).

Every model call uses the OpenRouter route in app.inference and reserves in the shared
ledger before it is sent. The direct OpenAI provider is never constructed and no fallback
route exists. The run stops at the buyer's cancellation, the active-time allowance and the
ledger's per-run spend and call caps. A sanitized trace (models, providers, usage, latency,
spend, versions; never prompts, answers or keys) is written under var/traces.
"""
import asyncio
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import threading
import time

from app import contracts_v2 as v2
from app import verification
from app.accounting import BudgetDenied, DEFAULT_BUYER_LIMITS
from app.assessment import _doubts, assess_full_product
from app.buyer_runs import RunError
from app.buyer_sources import (
    CORPUS_VERSION, FETCH_ROOM_S, ClassCellularsSource, DeliveryChoice, SourceRead, gate_fetcher,
)
from app.contract_interfaces import PlanningContext, WritingContext
from app.domestic_discovery import DISCOVERY_ENV, DomesticDiscovery
from app.inference import (
    ROOT, CallLog, InferenceUnavailable, TracedProvider, build_inference, config_path, load_env_file,
    load_inference_config,
)
from app.orchestrator import investigate
from app.writer import OpenAIWriter, deterministic_explanation

log = logging.getLogger('techscout.buyer_flow')

TRACE_VERSION = 1
MAX_FINALISTS = 3
MAX_UNCERTAIN = 6
# Seconds after the flow starts. app.accounting refuses a buyer job's non-final reservations
# that would end after 90 s and final-phase ones after 120 s; these leave a margin inside both.
RESEARCH_BY = 60
NONFINAL_BY = 85
FINAL_BY = 118
MIN_WRITER_SECONDS = 5

# Fixed buyer-facing stop reasons.
OUT_OF_SCOPE = ('This first live flow covers exact-model phone searches delivered in Lebanon, '
                'checked at Class Cellulars only')
NEEDS_CITY = 'Add a delivery city so the delivery route and its charges can be checked'
NO_PICKUP = 'Choosing a pickup branch is not supported yet; search for delivery instead'
NOT_CONFIGURED = 'The buyer worker is not configured for live searches'
SOURCE_FAILED = 'Class Cellulars could not be read in this run'
NO_LISTING = 'Class Cellulars lists no matching phone for this request'
UNREADABLE = ('Class Cellulars listed matching phones, but none could be read into the comparison '
              'format; the run trace names each listing and why')
NOTHING_ELIGIBLE = 'No listing at Class Cellulars meets the hard requirements'
NONE_VERIFIED = 'No offer could be verified in this run; each candidate lists what is still unknown'
VERIFIED = 'The final re-check confirmed {} offer(s) in this run'
LIMIT = 'Stopped at the run limit ({}) before the search finished'
# Fixed reasons for finalists that were not verified.
REASON_NOT_RECHECKED = 'not verified: no final re-check fit within the limits'
REASON_NOT_RETURNED = 'not verified: the final re-check did not return this listing'
REASON_RECHECK_FAILED = 'not verified: the final re-check did not confirm this offer'
REASON_STALE = 'not verified: evidence was not observed during this run'
REASON_UNREADABLE = 'not verified: the re-read listing could not be read into the comparison format'
# What stopped a run early, as recorded in the trace.
STOP_CAUSES = {'cancelled': 'cancelled or superseded by the buyer',
               'run_stopped': 'the run was closed by its active-time limit or the worker lost its lease',
               'deadline': "the flow's own deadline", 'spend': 'a ledger spend or call cap'}


class Stopped(Exception):
    """The buyer cancelled, the lease was lost or the publication was refused."""


def _utc_now():
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ResearchSetup:
    """What the orchestrator may dispatch: the research workers, their processing version and
    the menu of resources they can actually read for this run's listings."""
    workers: object
    processing: object
    resources: Callable  # (request, SourceRead) -> tuple[Resource, ...]


@dataclass
class FlowDependencies:
    ledger: object
    inference: object  # app.inference.BuyerInference
    source: object  # app.buyer_sources.ClassCellularsSource or a test double
    research: ResearchSetup | None = None
    traces: Path | None = None
    versions: dict = field(default_factory=dict)
    wall_clock: Callable[[], datetime] = _utc_now
    domestic_sources: object | None = None
    onboarding: object | None = None
    research_parallelism: int = 3
    retailer_browser: object | None = None  # Per-run session factory supplied by the shared adapter.
    tavily_preflight_ok: bool = False
    inspect_listing: Callable | None = None


def supported(request: v2.ShoppingRequest) -> str | None:
    """None when the request is in this flow's scope, else the buyer-facing reason."""
    if request.intent.kind != 'exact_model' or request.requirements.category != 'phone':
        return OUT_OF_SCOPE
    if request.fulfillment.mode == 'pickup':
        return NO_PICKUP
    if not request.fulfillment.destination.city:
        return NEEDS_CITY
    return None


def _code_version() -> str:
    explicit = os.environ.get('TECHSCOUT_CODE_VERSION', '').strip()
    if explicit:
        return explicit[:80]
    try:
        head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True,
                              timeout=3, check=True).stdout.strip()
        dirty = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=ROOT,
                               capture_output=True, text=True, timeout=3, check=True).stdout.strip()
        return head + ('+uncommitted' if dirty else '')
    except (OSError, subprocess.SubprocessError):
        return 'unknown'


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


_PRODUCTION: FlowDependencies | None = None
_PRODUCTION_LOCK = threading.Lock()


def worker_dependencies(*, checkpoint=False) -> FlowDependencies:
    """Built once per worker process (database engines, gate, providers); a failed build is
    retried on the next run, so fixing the configuration needs no restart. Changing a working
    configuration does: restart the worker."""
    global _PRODUCTION
    with _PRODUCTION_LOCK:
        if _PRODUCTION is None:
            _PRODUCTION = production_dependencies(require_inference=checkpoint)
        elif _PRODUCTION.inference is None:
            # A missing route may have been configured between runs. Reuse the same
            # gate, database engines and ledger when enabling the newly available route.
            load_env_file(names=('OPENROUTER_API_KEY',))
            path = config_path()
            inference = _configured_inference(path, required=checkpoint)
            if inference is not None:
                versions = dict(_PRODUCTION.versions, inference_config_sha256=_sha256(path))
                _PRODUCTION = replace(_PRODUCTION, inference=inference, versions=versions)
        return _PRODUCTION


def _configured_inference(path, *, required):
    try:
        return build_inference(load_inference_config(path))
    except InferenceUnavailable:
        if required:
            raise
        return None


def production_dependencies(*, require_inference=True, fixed_registry_path=None, inference=None,
                            load_worker_inference=True) -> FlowDependencies:
    """Wire the backend. The single-source checkpoint explicitly requires its inference route."""
    from app.accounting import Ledger
    from functools import partial
    from app.agent_inspection import FrozenInspectionRegistry, inspect_public_listing
    from app.corpus import Corpus
    from app.corpus_contracts import ProcessingVersion
    from app.directory import Directory
    from app.domestic_buyer_sources import DomesticBuyerSources
    from app.onboarding import ActivationRegistry, OnboardingQueue
    from app.embeddings import EmbeddingRoute, RATE_VARIABLE
    from app.research_workers import ResearchWorkers
    from app.source_registry import DEFAULT_REGISTRY
    from app.sources import SourceGate
    from app.storage import Storage
    load_env_file(names=('OPENROUTER_API_KEY', RATE_VARIABLE))
    path = config_path()
    if inference is None and load_worker_inference:
        inference = _configured_inference(path, required=require_inference)
    ledger = Ledger.configured()
    storage = Storage()
    registry = fixed_registry_path or ActivationRegistry(storage)
    inspection_registry = FrozenInspectionRegistry(storage, base=fixed_registry_path) if fixed_registry_path else registry
    directory = Directory(storage, registry_path=registry)
    gate = SourceGate(registry)
    source = ClassCellularsSource(gate_fetcher(gate, registry), storage=storage)
    # No reviewed shared extraction profile reads Class Cellulars pages yet, so the research
    # workers read only what the public corpus already holds for these listings; a field they
    # cannot settle stays an explicit gap, and they make no model call to pick a page to fetch.
    workers = ResearchWorkers(directory, Corpus(storage), gate, {}, embeddings=EmbeddingRoute.configured())
    processing = ProcessingVersion(corpus_version=CORPUS_VERSION, index_version=CORPUS_VERSION,
                                   extraction_version=source.adapter_version(), chunk_version='none')
    load_env_file(names=DISCOVERY_ENV)
    reviewed = json.loads((DEFAULT_REGISTRY.parent/'maintenance.json').read_text(encoding='utf-8'))
    workers.domestic_tools = DomesticDiscovery.from_env(gate, workers.directory, directory_defaults=reviewed['manufacturer_pages'])
    research = ResearchSetup(workers, processing, listing_resources)
    versions = dict(code=_code_version(), inference_config_sha256=_sha256(path),
                    source_registry_sha256=_sha256(Path(fixed_registry_path or DEFAULT_REGISTRY)),
                    source=dict(source_id=source.source_id, adapter_version=source.adapter_version()))
    return FlowDependencies(ledger=ledger, inference=inference, source=source, research=research,
                            traces=ROOT / 'var' / 'traces', versions=versions,
                            domestic_sources=DomesticBuyerSources(storage, gate, directory,
                                                                  fallback=workers.domestic_tools.fallback, embedding=workers.embeddings),
                            inspect_listing=partial(inspect_public_listing, registry=inspection_registry),
                            onboarding=None if fixed_registry_path else OnboardingQueue(storage, registry, directory=directory))


def listing_resources(request, read: SourceRead) -> tuple:
    """One research resource per discovered listing, naming the fields still open on it."""
    from app.corpus_contracts import ResourceKey
    from app.research_workers import Resource
    from app.source_capabilities import CapabilityRef
    from app.adapters.class_cellulars import CONFIG
    fixed = {p.page_url for p in CONFIG.products}
    resources = []
    for offer in read.offers[:20]:
        open_fields = [path for path, fact in (('condition', offer.condition),
                                               ('variant.specs.region', offer.variant.specs.region),
                                               ('variant.specs.sim', offer.variant.specs.sim),
                                               ('variant.specs.storage_gb', offer.variant.specs.storage_gb),
                                               ('variant.color', offer.variant.color))
                       if fact is None or fact.state != 'known']
        if not open_fields:  # nothing left to research on this listing
            continue
        url = str(offer.listing_url)
        key = ResourceKey(source_id=offer.source_id, url=url, representation='html',
                          product_id=offer.source_product_id, variant_id=offer.source_variant_id)
        capability = None if url in fixed else CapabilityRef(capability_id='class-product', version='1')
        resources.append(Resource(retailer_id=offer.retailer_id, key=key, content='listing',
                                  fields=tuple(open_fields), capability=capability))
    return tuple(resources)


def run(context) -> None:
    """Entry point for ``python -m app.run_worker --controller app.buyer_flow:run``."""
    _start(context, checkpoint=False)


def run_checkpoint(context) -> None:
    """Run the original single-source Class Cellulars live checkpoint (#75/#144)."""
    _start(context, checkpoint=True)


def _start(context, *, checkpoint):
    try:
        deps = worker_dependencies(checkpoint=checkpoint)
        if not checkpoint and context.request.implementation == 'single_agent':
            from app.shopping_agent import BUYER_SETTINGS, pinned_inference
            deps = replace(deps, inference=pinned_inference(), versions=dict(deps.versions,
                inference_config_sha256=_sha256(ROOT / 'config/evaluation.json'),
                shopping_agent_config_sha256=_sha256(BUYER_SETTINGS),
                inference_config_section='evaluation_inference', reasoning_effort='low'))
    except (InferenceUnavailable, BudgetDenied) as exc:
        # The operator reads the detail in the worker log; the buyer sees a fixed message.
        log.warning('Buyer worker is not configured: %s', exc)
        try:
            context.publish(status='error', stop_reason=NOT_CONFIGURED)
        except (RunError, ValueError):
            pass
        return
    execute(context, deps, checkpoint=checkpoint)


def execute(context, deps: FlowDependencies, *, checkpoint=False) -> None:
    if context.request.implementation == 'single_agent' and not checkpoint:
        from app.shopping_agent import ShoppingAgent
        ShoppingAgent(context, deps).run()
    elif deps.domestic_sources is not None and not checkpoint:
        from app.domestic_buyer import DomesticBuyerFlow
        DomesticBuyerFlow(context, deps).run()
    else:
        if checkpoint and deps.research:
            workers = replace(deps.research.workers, domestic_tools=None, discovery=None)
            deps = replace(deps, research=replace(deps.research, workers=workers))
        _Flow(context, deps).run()


class _Flow:
    model_writer = True

    def __init__(self, context, deps: FlowDependencies):
        self.context, self.deps = context, deps
        self.request: v2.ShoppingRequest = context.request
        self.run_ref: v2.RunReference = context.claim.run
        self.started = time.monotonic()
        self.started_at = deps.wall_clock()
        allowance = max(0.0, float(context.remaining_seconds()))
        self.final_by = min(FINAL_BY, allowance)
        self.nonfinal_by = min(NONFINAL_BY, self.final_by)
        self.stop, self.done = threading.Event(), threading.Event()
        self.calls = CallLog()
        self.roles = TracedProvider(deps.inference.roles, self.calls) if deps.inference is not None else None
        self.job = None
        self.limits: set[str] = set()
        self.trace = dict(trace_version=TRACE_VERSION, run=self.run_ref.model_dump(mode='json'),
                          request=dict(request_id=self.request.request_id, revision=self.request.revision,
                                       intent=self.request.intent.kind,
                                       category=self.request.requirements.category,
                                       model=getattr(self.request.intent, 'model', None)),
                          started_at=self.started_at.isoformat(),
                          limits=dict(active_seconds=round(allowance, 3), nonfinal_by_s=self.nonfinal_by,
                                      final_by_s=self.final_by, finalists=MAX_FINALISTS,
                                      ledger_buyer_caps=dict(**DEFAULT_BUYER_LIMITS.model_dump(mode='json'),
                                                             calls=40)),
                          versions=dict(deps.versions, inference=deps.inference.versions() if deps.inference else
                                        dict(provider='openrouter', status='unavailable', fallback='disabled')),
                          phases=[], source=None, research=None, offers=None, writer=None)

    # Time, cancellation and publication

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def left(self, by: float) -> float:
        return by - self.elapsed()

    def stopped(self) -> bool:
        """True once the buyer cancelled or the run's own deadline passed (each recorded)."""
        if not self.stop.is_set() and self.elapsed() >= self.final_by:
            self._halt('deadline')
        return self.stop.is_set()

    def _halt(self, cause: str) -> None:
        """Record why the run stopped, stop every role and close the ledger job to new spend,
        so no retry, redirect or model call can start after the stop."""
        self.limits.add(cause)
        self.stop.set()
        if self.job is not None:
            try:
                self.job.cancel()
            except Exception:  # noqa: BLE001 - the ledger's own job deadline still applies
                pass

    def cancelled(self) -> bool:
        """The run was stopped from outside: no publication can succeed any more."""
        return bool({'cancelled', 'run_stopped'} & self.limits)

    def _stop_cause(self) -> str:
        """'cancelled' when the buyer cancelled or refined the run; otherwise 'run_stopped'
        (active-time limit, lost lease or expired session)."""
        try:
            from sqlalchemy import select
            from app.storage import buyer_runs
            with self.context.service.storage.engine.connect() as db:
                status = db.execute(select(buyer_runs.c.status).where(
                    buyer_runs.c.run_id == self.run_ref.run_id)).scalar_one_or_none()
        except Exception:  # noqa: BLE001 - an unreadable cause is not blamed on the buyer
            status = None
        return 'cancelled' if status == 'cancelled' else 'run_stopped'

    def _watch(self):
        """Poll the owned run; cancellation or a lost lease stops every role and the ledger job.
        One failed check is retried before the run is treated as stopped."""
        failures = 0
        while not self.done.wait(.2):
            try:
                gone = self.context.cancelled()
                failures = 0
            except Exception:  # noqa: BLE001 - a second consecutive failure is a stopped run
                failures += 1
                gone = failures >= 2
                if not gone:
                    continue
            if self.done.is_set():  # the result was published while this check ran
                return
            if gone:
                self._halt(self._stop_cause())
                return
            if self.stopped():
                return

    def publish(self, **changes):
        if self.cancelled():
            raise Stopped()
        try:
            return self.context.publish(**changes)
        except RunError as exc:
            self.stop.set()
            raise Stopped() from exc

    def phase(self, name: str, **data):
        self.trace['phases'].append(dict(name=name, at_ms=round(self.elapsed() * 1000), **data))

    # The run

    def run(self):
        watcher = threading.Thread(target=self._watch, name='buyer-flow-watch', daemon=True)
        watcher.start()
        status, reason, snapshot = 'error', 'Shopping controller failed', None
        try:
            status, reason, snapshot = self._run()
        except Stopped:
            status = 'cancelled' if 'cancelled' in self.limits else 'stopped'
            reason = STOP_CAUSES['cancelled'] if status == 'cancelled' else STOP_CAUSES['run_stopped']
        except Exception as exc:  # noqa: BLE001 - recorded by type only, then published as an error
            self.trace['failure'] = type(exc).__name__
            log.exception('Buyer flow failed')
            try:
                self.publish(status='error', stop_reason='Shopping controller failed')
            except Stopped:
                pass
        finally:
            self.done.set()
            watcher.join(5)  # its last run check finishes before the controller returns
            self.trace.update(status=status, stop_reason=reason, latency_ms=round(self.elapsed() * 1000),
                              finished_at=self.deps.wall_clock().isoformat(),
                              stopped_by=sorted(self.limits), model_calls=self.calls.entries(),
                              spend=self._spend())
            self._write_trace()
        return snapshot

    def _run(self):
        request = self.request
        self.publish(status='running', phase='interpret')
        problem = supported(request)
        if problem is not None:
            self.phase('interpret', outcome='out_of_scope')
            return self._finish('abstained', problem)
        self.phase('interpret', outcome='in_scope')
        route = DeliveryChoice(request.fulfillment.destination.city)
        self.job = self.deps.ledger.open_job(self.run_ref.run_id)
        if self.cancelled():
            self.job.cancel()
            raise Stopped()
        if self.stopped():
            self.job.cancel()  # the deadline passed before the job existed to be closed
            return self._finish('partial', LIMIT.format('deadline'))

        # Discover: listings through the gate and the ledger (or stored by an earlier run).
        self.publish(status='running', phase='discover')
        read = self.deps.source.discover(request, route, self.job, self.run_ref,
                                         room=lambda: self.left(self.nonfinal_by))
        self.trace['source'] = dict(source_id=self.deps.source.source_id, mode=read.mode,
                                    fetches=list(read.fetches), errors=list(read.errors),
                                    skipped=list(read.skipped), refused=read.refused, offers=len(read.offers))
        self.phase('discover', mode=read.mode, offers=len(read.offers), errors=len(read.errors))
        if read.refused:
            self.limits.add('deadline')
        failed = None if read.offers else (UNREADABLE if read.skipped else SOURCE_FAILED if read.errors else None)
        self.outcomes = (v2.SourceOutcome(source_id=self.deps.source.source_id, name=self.deps.source.name,
                                          status='failed' if failed else 'checked', reason=failed),)
        if not read.offers:
            if read.refused:
                return self._finish('partial', LIMIT.format('deadline'))
            if read.skipped:  # listings exist but none could be read: a source-data gap, not "none"
                return self._finish('abstained', UNREADABLE)
            if read.errors:
                return self._finish('error', SOURCE_FAILED)
            return self._finish('abstained', NO_LISTING)
        # A provisional candidate shows when its price was originally observed (stored listings
        # keep their earlier time even though this run re-read the policy page).
        provisional = tuple(v2.ProvisionalCandidate(offer=o, observed_at=min(
            e.observed_at for e in o.evidence if e.evidence_id in set(o.price.evidence_ids) or not o.price.evidence_ids))
            for o in read.offers[:MAX_UNCERTAIN])
        self.coverage = v2.CoverageCounts(discovered=1, attempted=1, retrieved=1)
        self.publish(status='running', phase='research', coverage=self.coverage, provisional=provisional,
                     source_outcomes=self.outcomes)

        # Research: the orchestrator plans over the listings the workers can read.
        research = self._research(read)
        reports = research.reports if research is not None else ()
        searches = [r.discovery_report for r in reports if r.discovery_report is not None]
        if searches:
            self.trace['discovery_channels'] = [dict(attempts=[asdict(a) for a in s.attempts],
                                                     coverage=s.coverage(())) for s in searches]

        # Evaluate with the fixed rules, then choose the finalists.
        self.publish(status='running', phase='evaluate')
        now = self.deps.wall_clock()
        assessed = [(o, assess_full_product(request, o, now=now)) for o in read.offers]
        rejected = [o.offer_id for o, a in assessed if a.decision == 'rejected']
        candidates = [(o, a) for o, a in assessed if a.decision != 'rejected']
        candidates.sort(key=_rank)
        self.trace['offers'] = dict(discovered=len(read.offers), rejected=rejected,
                                    held_decisions={o.offer_id: a.decision for o, a in assessed})
        self.phase('evaluate', candidates=len(candidates), rejected=len(rejected))
        if not candidates:
            return self._finish('abstained', NOTHING_ELIGIBLE, provisional=())

        # Verify: the final phase re-reads the finalists live.
        finalists = candidates[:MAX_FINALISTS]
        recommendations, uncertain = self._verify(read, route, finalists)
        if searches:
            verified = tuple(offer for recommendation in recommendations for offer in recommendation.offers())
            # The first buyer flow sources its finalists before web research. A rediscovered
            # listing or retailer must not count as additional coverage from a search channel.
            initial_ids = {offer.offer_id for offer in read.offers}
            baseline = tuple(v for v in verified if v.offer.offer_id in initial_ids)
            for search, record in zip(searches, self.trace['discovery_channels']):
                record['coverage'] = search.coverage(verified, baseline=baseline)
        uncertain += [v2.UncertainAlternative(offer=o, reasons=(*_doubts(a), REASON_NOT_RECHECKED), cost=a.cost)
                      for o, a in candidates[MAX_FINALISTS:]]
        uncertain = uncertain[:MAX_UNCERTAIN]
        self.coverage = v2.CoverageCounts(discovered=1, attempted=1, retrieved=1,
                                          qualifying=1 if recommendations else 0)

        # Write: one tool-free writer call over the checked claims (or the deterministic summary).
        explanation = self._write(recommendations, uncertain)
        limit = self._limit()
        if recommendations:
            status, reason = ('partial', LIMIT.format(limit)) if limit else ('complete', VERIFIED.format(
                sum(len(r.offers()) for r in recommendations)))
        else:
            status, reason = ('partial', LIMIT.format(limit)) if limit else ('abstained', NONE_VERIFIED)
        return self._finish(status, reason, recommendations=tuple(recommendations), uncertain=tuple(uncertain),
                            provisional=(), explanation=explanation)

    def _limit(self) -> str | None:
        for name in ('cancelled', 'deadline', 'spend'):
            if name in self.limits:
                return name
        return None

    def _finish(self, status, reason, **results):
        fields = dict(status=status, stop_reason=reason, phase='write' if 'explanation' in results else None,
                      source_outcomes=getattr(self, 'outcomes', None), coverage=getattr(self, 'coverage', None),
                      **results)
        self.phase('publish', status=status)
        snapshot = self.publish(**{k: v for k, v in fields.items() if v is not None})
        self.done.set()  # the run is finished; its closed state is not a stop to record
        return status, reason, snapshot

    def _research(self, read: SourceRead):
        setup = self.deps.research
        resources = setup.resources(self.request, read) if setup is not None else ()
        budget_left = self.left(min(RESEARCH_BY, self.nonfinal_by))
        record = dict(resources=len(resources))
        self.trace['research'] = record
        if self.stopped() or budget_left <= 1:
            if not self.stopped():
                self.limits.add('deadline')
            record['skipped'] = 'deadline or cancellation'
            self.phase('research', outcome='skipped')
            return None
        workers = setup.workers if setup is not None else None
        if workers is not None and hasattr(workers, 'provider'):
            workers = replace(workers, provider=self.roles)
        deadline = self.deps.wall_clock() + timedelta(seconds=budget_left)
        try:
            result = investigate(PlanningContext(run=self.run_ref, request=self.request), job=self.job,
                                 workers=workers, provider=self.roles, resources=resources,
                                 processing=setup.processing if setup is not None else None,
                                 deadline=deadline)
        except Exception as exc:  # noqa: BLE001 - research never decides an offer; record and go on
            record['failure'] = type(exc).__name__
            self.phase('research', outcome='failed')
            return None
        record.update(reports=len(result.reports), gaps=list(result.gaps)[:40],
                      critique=None if result.critique is None else dict(
                          status=result.critique.status, issues=len(result.critique.issues),
                          follow_up_attempted=result.critique.follow_up_attempted,
                          reason=result.critique.reason),
                      trace=[dict(action=s.action, status=s.status, source_id=s.source_id)
                             for r in result.reports for s in r.trace][:40])
        if any('deadline' in g.lower() for g in result.gaps):
            self.limits.add('deadline')
        self._note_denials()
        self.phase('research', outcome='completed', reports=len(result.reports))
        return result

    def _note_denials(self):
        if any(c['status'] == 'budget_denied' for c in self.calls.entries()):
            self.limits.add('spend')

    def _verify(self, read: SourceRead, route, finalists):
        request, run = self.request, self.run_ref
        try:
            self.job.enter_final_phase()
        except BudgetDenied:
            pass
        self.publish(status='running', phase='verify')
        # A re-read starts only while one full gate request (queue wait plus call timeout)
        # still fits before the flow's own final deadline.
        if self.stopped() or self.left(self.final_by) < FETCH_ROOM_S:
            if not self.stopped():
                self.limits.add('deadline')
            self.phase('verify', outcome='skipped')
            return [], [v2.UncertainAlternative(offer=o, reasons=(*_doubts(a), REASON_NOT_RECHECKED), cost=a.cost)
                        for o, a in finalists]
        ids = [o.offer_id for o, _ in finalists]
        try:
            fresh = self.deps.source.refresh(request, route, self.job, run, ids, earlier=read,
                                             room=lambda: self.left(self.final_by))
        except Exception as exc:  # noqa: BLE001 - a failed re-read verifies nothing; it is not a crash
            self.trace['verify_failure'] = type(exc).__name__
            fresh = SourceRead(mode='refresh', errors=(f'final re-read failed ({type(exc).__name__})',))
        if fresh.refused:
            self.limits.add('deadline')
        unreadable = fresh.skipped_ids()
        checked_at = self.deps.wall_clock()
        run_replies = set(read.replies) | set(fresh.replies)
        policy_replies = {(p.response_sha256, p.observed_at) for p in read.policies}
        returned = {o.offer_id: o for o in fresh.offers}
        recommendations, uncertain, outcomes = [], [], {}
        for offer, held in finalists:
            again = returned.get(offer.offer_id)
            if again is None:
                missing = REASON_UNREADABLE if offer.offer_id in unreadable else REASON_NOT_RETURNED
                outcomes[offer.offer_id] = 'unreadable' if offer.offer_id in unreadable else 'not_returned'
                uncertain.append(v2.UncertainAlternative(offer=offer, reasons=(*_doubts(held), missing),
                                                         cost=held.cost))
                continue
            problem = verification.recheck_problem(fresh.originals[offer.offer_id], run_replies=run_replies,
                                                   final_replies=set(fresh.replies), policy_replies=policy_replies,
                                                   now=checked_at)
            assessment = assess_full_product(request, again, now=checked_at)
            if problem is None and assessment.decision == 'recommendable' and len(recommendations) < MAX_FINALISTS:
                try:
                    verified = v2.VerifiedOffer(offer=again, cost=assessment.cost,
                                                verification=v2.Verification(checked_at=checked_at, run=run))
                except ValueError:
                    problem = 'contract'
                else:
                    outcomes[offer.offer_id] = 'verified'
                    recommendations.append(v2.Recommendation(primary=verified))
                    continue
            if assessment.decision == 'rejected':
                outcomes[offer.offer_id] = 'rejected_on_recheck'
                continue
            reason = (REASON_STALE if problem == verification.STALE
                      else REASON_RECHECK_FAILED if problem is not None else None)
            outcomes[offer.offer_id] = 'unresolved' if reason is None else problem
            uncertain.append(v2.UncertainAlternative(offer=again, cost=assessment.cost,
                                                     reasons=(*_doubts(assessment), *((reason,) if reason else ()))))
        self.trace['verify'] = dict(finalists=ids, outcomes=outcomes, fetches=list(fresh.fetches),
                                    errors=list(fresh.errors), skipped=list(fresh.skipped), refused=fresh.refused,
                                    checked_at=checked_at.isoformat())
        self.phase('verify', verified=len(recommendations), rechecked=len(ids))
        return recommendations, uncertain

    def _write(self, recommendations, uncertain):
        context = WritingContext(run=self.run_ref, recommendations=tuple(recommendations), request=self.request,
                                 coverage=self.coverage, uncertain=tuple(uncertain))
        seconds = min(30.0, self.left(self.final_by) - 1)
        self.publish(status='running', phase='write')
        if not self.model_writer or self.stopped() or seconds < MIN_WRITER_SECONDS or self.deps.inference is None:
            if seconds < MIN_WRITER_SECONDS and not self.stopped():
                self.limits.add('deadline')
            reason = 'OpenRouter inference route unavailable' if self.deps.inference is None else 'no time left for the writer'
            if not self.model_writer:
                reason = 'Evaluation configuration uses a deterministic summary'
            explanation = deterministic_explanation(context)
            self.trace['writer'] = dict(source='fallback', reason=reason, status='not_called')
            return v2.RunExplanation(text=explanation.text, evidence_ids=explanation.evidence_ids, source='fallback')
        provider = TracedProvider(self.deps.inference.writer(seconds), self.calls, role='writer')
        writer = OpenAIWriter.for_job(self.job, seconds=seconds, provider=provider, cancelled=self.stopped)
        result = asyncio.run(writer.explain(context))
        # The writer's ledger lease and provider wait both equal this, inside the final deadline.
        self.trace['writer'] = dict(result.trace_data(), lease_seconds=round(seconds, 3),
                                    final_deadline_left_s=round(self.left(self.final_by), 3))
        self._note_denials()
        self.phase('write', source=result.source, status=result.status)
        return v2.RunExplanation(text=result.explanation.text, evidence_ids=result.explanation.evidence_ids,
                                 source=result.source)

    # Trace

    def _spend(self) -> dict:
        """This run's ledger attempts: known spend, uncertain bounds kept, calls and fetches."""
        if self.job is None:
            return dict(ledger='not_opened')
        try:
            attempts = self.deps.ledger.job_attempts(self.run_ref.run_id)
        except Exception as exc:  # noqa: BLE001
            return dict(ledger='unreadable', error=type(exc).__name__)
        known, uncertain, held, counts = Decimal(0), Decimal(0), Decimal(0), {}
        for attempt in attempts:
            counts[attempt['kind']] = counts.get(attempt['kind'], 0) + (attempt['status'] != 'released')
            amount = Decimal(str(attempt['values'][3]))
            if attempt['status'] == 'completed':
                known += amount
            elif attempt['status'] == 'uncertain':
                uncertain += amount
            elif attempt['status'] in ('held', 'sent'):
                held += amount
        return dict(ledger='read', known_usd=str(known), uncertain_usd=str(uncertain),
                    still_held_usd=str(held), attempts=counts,
                    note='uncertain amounts are full reservations kept because the cost was not reported')

    def _write_trace(self):
        directory = self.deps.traces
        if directory is None:
            return
        try:
            directory.mkdir(parents=True, exist_ok=True)
            name = f'{self.run_ref.run_id}-r{self.run_ref.run_revision}.json'
            (directory / name).write_text(json.dumps(self.trace, indent=2, sort_keys=True, default=str),
                                          encoding='utf-8')
        except OSError:
            log.warning('Could not write the run trace')


def _rank(pair):
    """Recommendable on held evidence first (lowest verified total), then the lowest known price."""
    offer, assessment = pair
    price = offer.price.value.amount if offer.price.state == 'known' else Decimal('Infinity')
    if assessment.decision == 'recommendable':
        total = assessment.cost.budget_conversion.quote if assessment.cost.budget_conversion else assessment.cost.total
        return (0, total.amount, offer.offer_id)
    return (1, price, offer.offer_id)
