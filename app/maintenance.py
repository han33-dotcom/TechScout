"""Opt-in domestic discovery, catalog and offer maintenance in a separate local process."""
import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import time
from uuid import uuid4

from pydantic import Field

from app.accounting import BudgetDenied, Ledger
from app.bootstrap import Manifest, Seed
from app.contracts import Model
from app.corpus import Corpus
from app.directory import Directory
from app.domestic_catalog import CATEGORIES, DomesticCatalog
from app.domestic_discovery import DISCOVERY_ENV, DomesticDiscovery
from app.embeddings import EmbeddingRoute, RATE_VARIABLE
from app.inference import InferenceUnavailable, build_inference, load_env_file, load_inference_config
from app.maintenance_discovery import discover
from app.onboarding import ActivationRegistry, OnboardingQueue
from app.refresh import refresh
from app.search_connectors import DirectoryPage
from app.sources import SourceGate
from app.storage import Storage

ROOT = Path(__file__).resolve().parents[1]
KINDS = ('discovery', 'catalog', 'offers')


class Schedule(Model):
    interval_seconds: int = Field(ge=60, le=30 * 86400)
    pages: int = Field(ge=1, le=100)
    calls: int = Field(ge=1, le=100)
    seconds: int = Field(ge=20, le=1800)
    usd: Decimal = Field(ge=0, le=3)

    def envelope(self):
        return self.model_dump(mode='json', include={'calls', 'seconds', 'usd'})


class Settings(Model):
    discovery: Schedule
    catalog: Schedule
    offers: Schedule
    manufacturer_pages: tuple[DirectoryPage, ...] = Field(max_length=10)


def initial():
    return dict(enabled=False, jobs={k: dict(due=0, cursor=0) for k in KINDS},
                gaps={}, use={}, listings={})


def control(ledger, enabled):
    with ledger._transaction() as (state, now):
        schedule = state.setdefault('maintenance_schedule', initial())
        schedule['enabled'] = enabled
        if not enabled:
            for slot in schedule['jobs'].values():
                active = slot.get('active')
                if active and active['job'] in state['jobs']:
                    state['jobs'][active['job']]['cancelled'] = True
        return dict(enabled=enabled)


def signal(ledger, category, *, gap=True, catalog_urls=()):
    """Aggregate only scoped catalog hits and category counts for the enabled scheduler."""
    if category not in CATEGORIES:
        raise ValueError('Unknown domestic category')
    with ledger._transaction() as (state, now):
        schedule = state.get('maintenance_schedule')
        if not schedule or not schedule['enabled']:
            return
        if gap:
            schedule['gaps'][category] = min(schedule['gaps'].get(category, 0) + 1, 1000)
        for url in tuple(catalog_urls)[:20]:
            digest = sha256(str(url).encode()).hexdigest()
            usage = schedule['use']
            if len(usage) < 3000 or digest in usage:
                usage[digest] = now


def status(ledger):
    with ledger._transaction() as (state, now):
        schedule = state.get('maintenance_schedule', initial())
        return dict(enabled=schedule['enabled'], gaps=schedule['gaps'],
                    admitted_listing_urls=len(schedule['listings']),
                    jobs={kind: dict(next_due=slot['due'], overdue_seconds=max(0, now-slot['due'])
                          if slot['due'] else 0, active=slot.get('active', {}).get('job'),
                          last=slot.get('last')) for kind, slot in schedule['jobs'].items()})


class Maintenance:
    def __init__(self, ledger, corpus, gate, manifest, settings, *, discovery, queue, roles=None, embedding=None):
        self.ledger, self.corpus, self.gate = ledger, corpus, gate
        self.manifest, self.settings = manifest, settings
        self.discovery, self.queue, self.roles, self.embedding = discovery, queue, roles, embedding
        self.catalog = DomesticCatalog(corpus.storage, registry_path=gate._registry_path)

    def claim(self, kind, force):
        limits = getattr(self.settings, kind)
        with self.ledger._transaction() as (state, now):
            schedule = state.setdefault('maintenance_schedule', initial())
            slot = schedule['jobs'][kind]
            if not schedule['enabled']:
                return
            active = slot.get('active')
            if active and active['lease'] > now:
                return
            if not active:
                gap_due = (kind == 'discovery' and schedule['gaps'] and now >= slot.get('gap_after', 0))
                if not force and now < slot['due'] and not gap_due:
                    return
                batch = kind + '-' + uuid4().hex
                active = dict(batch=batch, job=('discovery:' if kind == 'discovery' else 'refresh:') + batch,
                              cursor=slot['cursor'], started=now, gap_counts=dict(schedule['gaps']))
                if kind != 'discovery':
                    seeds = {str(s.url): s for s in self.manifest.seeds if (s.format == 'catalog') == (kind == 'catalog')}
                    if kind == 'offers':
                        for url, raw in schedule['listings'].items():
                            seeds.setdefault(url, Seed.model_validate(raw))
                    active['seeds'] = [s.model_dump(mode='json') for s in seeds.values()]
                    active['high'] = [url for url in seeds if schedule['use'].get(sha256(url.encode()).hexdigest(), 0)
                                      > now - 7 * 86400]
                slot['active'] = active
            active.update(owner=uuid4().hex, lease=now + max(125, limits.seconds + 5))
            return dict(active)

    def run(self, kind, *, force=False):
        active = self.claim(kind, force)
        if not active:
            return None
        limits = getattr(self.settings, kind)
        job = self.ledger.open_job(active['job'], 'maintenance', envelope=limits.envelope())
        started = datetime.fromtimestamp(active['started'], timezone.utc)
        report, additions = {}, {}
        try:
            with self.ledger._transaction() as (state, now):
                schedule = state['maintenance_schedule']
                current = schedule['jobs'][kind]['active']
                if current['owner'] != active['owner'] or not schedule['enabled']:
                    raise BudgetDenied('Schedule paused or lease lost')
                if kind == 'discovery':
                    if current.get('dispatched'):
                        raise BudgetDenied('Interrupted discovery; prior dispatch retained')
                    current['dispatched'] = True
            before = self.catalog.coverage(as_of=started)['categories']
            if kind == 'discovery':
                order = [CATEGORIES[(active['cursor'] + i) % len(CATEGORIES)] for i in range(len(CATEGORIES))]
                category = min(order, key=lambda c: (-active['gap_counts'].get(c, 0),
                                                     before[c]['domestic_variants_with_two_sellers']))
                report = discover(job, category=category, cursor=active['cursor'] * limits.pages, coverage=before[category],
                                  discovery=self.discovery, queue=self.queue, roles=self.roles,
                                  steps=min(5, limits.pages))
            elif active['seeds']:
                manifest = self.manifest.model_copy(update={'seeds': tuple(Seed.model_validate(s) for s in active['seeds'])})
                result = refresh(manifest, ledger=self.ledger, corpus=self.corpus, gate=self.gate, batch_id=active['batch'],
                                 limit=limits.pages, high_use=active['high'], embedding=self.embedding,
                                 envelope=limits.envelope(), rotation=active['cursor'])
                counts = Counter(result['counts'])
                pages = result['pages']
                for page in pages.values():
                    additions.update({u: s for u, s in page.get('product_links', {}).items() if s})
                report = dict(counts={name: counts[name] for name in ('new','changed','unchanged','removed','failed',
                                  'skipped','reprocessed','superseded','interrupted','pending')},
                              checked=sum(p.get('attempts', 0) > 0 for p in pages.values()),
                              overdue=result['plan']['remaining_due'] + counts['pending'] + counts['skipped'],
                              ineligible=sum(r['state'] == 'ineligible' for r in result['plan']['resources']),
                              enumerated_product_urls=len({u for p in pages.values() for u in p.get('product_links', {})}),
                              exact_listings_extracted=sum(p.get('exact_listing_extracted', False) for p in pages.values()),
                              stop_reason=result['stop_reason'], extractions=result['extractions'],
                              embedding_calls=result['embedding_calls'], pages={url: {k:v for k,v in page.items()
                              if k != 'product_links'} for url,page in pages.items()})
            else:
                report = dict(stop_reason='no eligible seeds', overdue=0)
            with self.ledger._transaction() as (_, now):
                ended = datetime.fromtimestamp(now, timezone.utc)
            after = self.catalog.coverage(as_of=ended)['categories']
            report['coverage_change'] = {c: {field: after[c][field] - before[c][field] for field in
                ('recent_priced_domestic_sellers','domestic_variants_with_two_sellers')} for c in CATEGORIES}
        except (BudgetDenied, ValueError, PermissionError) as exc:
            report = dict(stop_reason=str(exc)[:200], failed=True)
        with self.ledger._transaction() as (state, now):
            schedule = state['maintenance_schedule']
            slot = schedule['jobs'][kind]
            if slot['active']['owner'] != active['owner']:
                raise BudgetDenied('Lost maintenance lease')
            attempts = [a for a in state['attempts'].for_job(job.run_id) if a['status'] != 'released']
            report.update(job_id=job.run_id, started_at=started.isoformat(),
                          finished_at=datetime.fromtimestamp(now, timezone.utc).isoformat(), calls=len(attempts),
                          spend_usd=str(sum((Decimal(a['values'][3]) for a in attempts if a['status']=='completed'), Decimal(0))),
                          unresolved_usd=str(sum((Decimal(a['values'][3]) for a in attempts if a['status']!='completed'), Decimal(0))))
            for url, raw in additions.items():
                if len(schedule['listings']) < 3000 - len(self.manifest.seeds):
                    schedule['listings'].setdefault(url, raw)
            if kind == 'discovery' and 'category' in report:
                category = report['category']
                remaining = schedule['gaps'].get(category, 0) - active['gap_counts'].get(category, 0)
                if remaining > 0:
                    schedule['gaps'][category] = remaining
                else:
                    schedule['gaps'].pop(category, None)
            slot.pop('active')
            slot.update(last=report, due=now + limits.interval_seconds, cursor=slot['cursor'] + 1,
                        gap_after=now + min(3600, limits.interval_seconds))
            return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=ROOT/'config/domestic-seller-coverage.json')
    parser.add_argument('--settings', type=Path, default=ROOT/'config/maintenance.json')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('enable','pause','status','tick','serve'):
        commands.add_parser(name)
    commands.add_parser('trigger').add_argument('kind', choices=KINDS)
    commands.add_parser('gap').add_argument('category', choices=CATEGORIES)
    args = parser.parse_args(argv)
    ledger = Ledger.configured()
    storage = Storage()
    try:
        if args.command in ('enable', 'pause'):
            result = control(ledger, args.command == 'enable')
        elif args.command == 'status':
            result = status(ledger)
        elif args.command == 'gap':
            signal(ledger, args.category)
            result = status(ledger)
        else:
            settings = Settings.model_validate_json(args.settings.read_bytes())
            manifest = Manifest.model_validate_json(args.manifest.read_bytes())
            registry = ActivationRegistry(storage)
            gate, directory = SourceGate(registry), Directory(storage, registry_path=registry)
            load_env_file(names=('OPENROUTER_API_KEY',RATE_VARIABLE,*DISCOVERY_ENV))
            env = dict(os.environ)
            env.setdefault('TECHSCOUT_MANUFACTURER_DIRECTORIES', json.dumps(settings.model_dump(mode='json')['manufacturer_pages']))
            try:
                roles = build_inference(load_inference_config()).roles
            except InferenceUnavailable:
                roles = None
            runner = Maintenance(ledger, Corpus(storage), gate, manifest, settings, roles=roles,
                    discovery=DomesticDiscovery.from_env(gate,directory,environ=env), embedding=EmbeddingRoute.configured(),
                    queue=OnboardingQueue(storage,registry,directory=directory))
            if args.command == 'trigger':
                result = runner.run(args.kind, force=True)
            else:
                while True:
                    result = {k: runner.run(k) for k in KINDS}
                    if args.command == 'tick':
                        break
                    if any(result.values()):
                        print(json.dumps(result), flush=True)
                    time.sleep(60)
        print(json.dumps(result, indent=2))
    finally:
        storage.engine.dispose()
        ledger.storage.engine.dispose()


if __name__ == '__main__':
    main()
