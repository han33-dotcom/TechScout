"""Plan or explicitly run a bounded refresh of a reviewed domestic catalog manifest."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from app.accounting import BudgetDenied, Ledger
from app.adapters.woo_catalog import _records
from app.bootstrap import DOMESTIC, RETIRED_IMPORT_SOURCES, Manifest, SeedBudget, extract_seed, seed_processing, seed_target
from app.corpus import Corpus
from app.corpus_contracts import DocumentCapture
from app.directory import Directory
from app.onboarding import ActivationRegistry
from app.source_registry import get_active_source
from app.sources import CacheValidators, MAX_RESPONSE_BYTES, SourceGate
from app.storage import Storage


def cadence(seed, high_use):
    if seed.content == 'policy':
        return timedelta(days=7)
    if seed.content == 'specification':
        return timedelta(days=30)
    if seed.format == 'catalog':
        return timedelta(hours=6)
    spread = int.from_bytes(sha256(str(seed.url).encode()).digest()[:4], 'big')
    return timedelta(hours=12 + spread % 13 if high_use else 72 + spread % 97)


def target(seed, corpus, gate):
    if seed.source_id in RETIRED_IMPORT_SOURCES:
        raise ValueError('Retired international source')
    key, profile = seed_target(seed, corpus, gate._registry_path)
    if not profile and seed.source_id not in DOMESTIC:
        raise ValueError('Unknown domestic source')
    seller = seed.listing or seed.dispatch_for
    directory = Directory(corpus.storage, registry_path=gate._registry_path)
    record = directory.get(seller.retailer_id if seller else seed.source_id)
    if (not record or not record.background_collection or record.entry.discovery_status == 'retired'
            or record.role != 'domestic' or record.source_country != 'LB' or record.source_id != seed.source_id):
        raise PermissionError('Domestic background collection is disabled for this retailer')
    options = dict(expect='json' if seed.format == 'catalog' else 'html',
                   capability=seed.capability or (profile.capability if seed.listing else None))
    gate.check_scope(seed.source_id, str(seed.url), **options)
    return key, profile, options


def plan(manifest, corpus, gate, *, now, limit, high_use=()):
    if now.utcoffset() is None or not 1 <= limit <= 100:
        raise ValueError('Use an aware planning time and a batch limit of 1–100')
    urls = {str(seed.url) for seed in manifest.seeds}
    if len(urls) != len(manifest.seeds) or set(high_use) - urls:
        raise ValueError('Refresh URLs must be unique and high-use URLs must belong to the manifest')
    rows = []
    for seed in manifest.seeds:
        url = str(seed.url)
        row = dict(url=url, source=seed.source_id, category=seed.category, high_use=url in high_use,
                   checked_at=None, due_at=None, state='new')
        rows.append(row)
        try:
            key, _, _ = target(seed, corpus, gate)
            previous = corpus.state(key)
        except (ValueError, PermissionError) as exc:
            row.update(state='ineligible', reason=str(exc))
            continue
        if previous:
            row['checked_at'] = previous.checked_at.isoformat()
            retry = previous.check_status in {'failed', 'denied', 'absent'}
            interval = timedelta(hours=6) if retry else cadence(seed, row['high_use'])
            due = previous.checked_at + interval
            row['due_at'] = due.isoformat()
            if previous.retirement_status:
                row.update(state='retired', due_at=None)
            elif retry:
                row['state'] = 'due' if due <= now else 'backoff'
            elif previous.needs_processing or previous.processing != seed_processing(manifest, seed):
                row['state'] = 'processing_due'
            else:
                row['state'] = 'due' if due <= now else 'deferred'
    candidates = sorted((row for row in rows if row['state'] in {'new', 'due', 'processing_due'}),
                        key=lambda row: (row['due_at'] or '', not row['high_use'], row['url']))
    selected = [row['url'] for row in candidates[:limit]]
    return dict(planned_at=now.isoformat(), resources=rows, selected=selected,
                due=len(candidates), remaining_due=len(candidates) - len(selected))


def check_page(seed, manifest, corpus, gate, budget, rows_left):
    key, profile, options = target(seed, corpus, gate)
    policy = get_active_source(seed.source_id, gate._registry_path)
    previous = corpus.state(key)
    if previous and previous.retirement_status:
        return dict(status='skipped', reason='Documented retirement supersedes this selection')
    processing = seed_processing(manifest, seed)
    validators = None
    if previous and previous.conditional_url and previous.processing == processing:
        try:
            corpus.load_raw(previous.document_id)
        except (FileNotFoundError, KeyError, ValueError):
            pass  # A full response can restore a missing or damaged local snapshot.
        else:
            validators = CacheValidators(previous.conditional_url, previous.etag, previous.last_modified)
    outcome = gate.fetch(budget, seed.source_id, str(seed.url), validators=validators, **options)
    checked = outcome.observed_at
    if checked is None:
        with budget.ledger._transaction() as (_, now):
            checked = datetime.fromtimestamp(now, timezone.utc)
    result = dict(status='failed', checked_at=checked.isoformat(), attempts=outcome.attempts,
                  http_status=outcome.status_code, bytes=len(outcome.body or b''), rows=0, extractions=0)
    if outcome.errors:
        result['errors'] = [error.code for error in outcome.errors]
        if outcome.attempts:
            corpus.record_failed_check(key, checked)
        else:
            result['status'] = 'skipped'
        return result
    if outcome.status_code == 304:
        applied = validators and outcome.final_url == validators.url and corpus.record_not_modified(key, previous, checked)
        result['status'] = 'unchanged' if applied else 'superseded'
        if applied:
            result['sha256'] = previous.raw_sha256
        return result
    try:
        count = len(_records(outcome, limit=100)) if seed.format == 'catalog' else 1
        result['rows'] = min(rows_left, count)
        if count > rows_left:
            result.update(status='skipped', reason='Catalog exceeds remaining record allowance')
            return result
        capture = DocumentCapture(key=key, body=outcome.body, relevant_content=outcome.body, origin='maintenance',
                                  checked_at=checked, observed_at=checked, final_url=outcome.final_url, visibility='public',
                                  processing=processing, etag=outcome.validators.etag if outcome.validators else None,
                                  last_modified=outcome.validators.last_modified if outcome.validators else None)

        def extract(page):
            result['extractions'] += 1
            return extract_seed(page, outcome, seed, profile, policy, rows_left)

        receipt = corpus.ingest(capture, extract, compare_extracted=True)
    except ValueError as exc:
        corpus.record_failed_check(key, checked)
        result['reason'] = str(exc)[:200]
        return result
    if receipt.status == 'stale':
        result['status'] = 'superseded'
        return result
    status = receipt.status
    if status != 'new' and previous and (processing != previous.processing or previous.needs_processing):
        status = 'reprocessed'
    result.update(status=status, changed_fields=list(receipt.changed_fields),
                  changed_passages=receipt.changed_passages, sha256=outcome.response_sha256)
    return result


def refresh(manifest, *, ledger, corpus, gate, batch_id, limit, high_use=()):
    """Freeze the first selection. A recovered lease never repeats an unacknowledged read."""
    if not batch_id.strip() or len(batch_id) > 100:
        raise ValueError('Provide a stable batch ID of 1–100 characters')
    fingerprint = sha256(json.dumps([manifest.model_dump(mode='json'), limit, sorted(set(high_use))],
                                    sort_keys=True).encode()).hexdigest()
    job = ledger.open_job('refresh:' + batch_id, 'maintenance')
    token = uuid4().hex
    # Planning reads the corpus outside the accounting transaction (they can be separate databases).
    with ledger._transaction() as (state, now):
        saved = state.get('refresh_batches', {}).get(job.run_id)
        planned_at = datetime.fromtimestamp(now, timezone.utc)
    selection = saved['plan'] if saved else plan(manifest, corpus, gate, now=planned_at, limit=limit, high_use=high_use)
    with ledger._transaction() as (state, now):
        record = state.setdefault('refresh_batches', {}).setdefault(job.run_id, dict(
            digest=fingerprint, plan=selection, pages={}, tries={}, lease_until=0,
            deadline=now + manifest.limits.max_elapsed_ms / 1000,
            all=dict(fetch=0, paid=0, usd='0', ms=0, records=0, bytes=0)))
        if record['digest'] != fingerprint:
            raise ValueError('This batch ID is bound to a different refresh request')
        if now < record['lease_until']:
            raise BudgetDenied('Another worker owns this refresh batch')
        record.pop('stop_reason', None)
        record.update(owner=token, lease_until=now + 120)
        selected = tuple(record['plan']['selected'])
        for page in record['pages'].values():
            if page['status'] == 'in_flight':
                page.update(status='interrupted', reason='Unconfirmed read after worker interruption')
    seeds = {str(seed.url): seed for seed in manifest.seeds}
    try:
        for url in selected:
            with ledger._transaction() as (state, now):
                record = state['refresh_batches'][job.run_id]
                if record['owner'] != token or now >= record['lease_until']:
                    raise BudgetDenied('Refresh lease lost')
                if url in record['pages']:
                    continue
                usage = record['all']
                remaining = manifest.limits.max_records - usage['records']
                if (state['jobs'][job.run_id]['cancelled'] or state['blocked'] or now >= record['deadline']
                        or remaining <= 0 or usage['fetch'] >= manifest.limits.max_fetch_attempts
                        or usage['bytes'] + MAX_RESPONSE_BYTES > manifest.limits.max_response_bytes):
                    record['stop_reason'] = 'Cancellation, deadline or collection limit reached'
                    break
                seed = seeds[url]
                row_hold = min(100, remaining) if seed.format == 'catalog' else 1
                record['lease_until'] = now + 120
                usage['records'] += row_hold
                usage['bytes'] += MAX_RESPONSE_BYTES
                record['pages'][url] = dict(status='in_flight')
            budget = SeedBudget(job, manifest, seed, token, gate._registry_path, checkpoints='refresh_batches')
            try:
                page = check_page(seed, manifest, corpus, gate, budget, remaining)
            except (ValueError, PermissionError, BudgetDenied) as exc:
                page = dict(status='skipped', reason=str(exc)[:200])
            with ledger._transaction() as (state, now):
                record = state['refresh_batches'][job.run_id]
                if record['owner'] != token:
                    raise BudgetDenied('Refresh lease lost')
                record['pages'][url] = page
                record['all']['bytes'] -= MAX_RESPONSE_BYTES - page.get('bytes', 0)
                record['all']['records'] -= row_hold - page.get('rows', 0)
    finally:
        with ledger._transaction() as (state, now):
            record = state['refresh_batches'][job.run_id]
            if record['owner'] == token:
                record.update(owner=None, lease_until=0)
    state = ledger.snapshot()
    record = state['refresh_batches'][job.run_id]
    pages = record['pages']
    counts = Counter(page['status'] for page in pages.values())
    counts['pending'] = len(selected) - len(pages)
    attempts = [a for a in state['attempts'].values() if a['job'] == job.run_id and a['status'] != 'released']
    spend = sum((Decimal(a['values'][3]) for a in attempts if a['status'] == 'completed'), Decimal(0))
    held = sum((Decimal(a['values'][3]) for a in attempts if a['status'] != 'completed'), Decimal(0))
    return dict(job_id=job.run_id, plan=record['plan'], pages=pages, counts=dict(counts),
                source_attempts=len(attempts), extractions=sum(p.get('extractions', 0) for p in pages.values()),
                embedding_calls=0, spend_usd=str(spend), unresolved_reservations_usd=str(held),
                stop_reason=record.get('stop_reason'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--limit', type=int, default=5, choices=range(1, 101), metavar='1-100')
    parser.add_argument('--high-use', action='append', default=[], metavar='MANIFEST_URL')
    parser.add_argument('--run-id', help='Execute or resume this exact batch. Omit to print the plan.')
    args = parser.parse_args(argv)
    manifest = Manifest.model_validate_json(args.manifest.read_bytes())
    storage = Storage()
    gate = SourceGate(ActivationRegistry(storage))
    ledger = None
    try:
        corpus = Corpus(storage)
        if args.run_id is None:
            report = plan(manifest, corpus, gate, now=datetime.now(timezone.utc),
                          limit=args.limit, high_use=args.high_use)
        else:
            ledger = Ledger.configured()
            report = refresh(manifest, ledger=ledger, corpus=corpus, gate=gate, batch_id=args.run_id,
                             limit=args.limit, high_use=args.high_use)
        print(json.dumps(report, indent=2))
        incomplete = {'failed', 'skipped', 'superseded', 'pending', 'interrupted'}
        return int(any(report.get('counts', {}).get(status, 0) for status in incomplete))
    finally:
        storage.engine.dispose()
        if ledger:
            ledger.storage.engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
