"""Run one bounded rotation of domestic discovery channels for a public catalog objective."""
import argparse
from dataclasses import asdict
import json
from typing import get_args

from app.accounting import Ledger
from app.contracts_v2 import Category
from app.directory import Directory
from app.domestic_discovery import CHANNELS, DISCOVERY_ENV, DomesticDiscovery, SearchObjective
from app.inference import load_env_file
from app.onboarding import ActivationRegistry, OnboardingQueue
from app.sources import SourceGate
from app.storage import Storage


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id', required=True, help='Stable identifier in the shared maintenance ledger')
    parser.add_argument('--query', required=True)
    parser.add_argument('--objective', required=True)
    parser.add_argument('--category', choices=get_args(Category), required=True)
    parser.add_argument('--language', choices=('en', 'ar'), default='en')
    parser.add_argument('--cursor', type=int, default=0, help='Starting channel in the rotation')
    parser.add_argument('--count', type=int, choices=range(1, len(CHANNELS) + 1), default=2)
    parser.add_argument('--limit', type=int, choices=range(1, 21), default=10)
    args = parser.parse_args(argv)
    if args.cursor < 0:
        parser.error('--cursor must be nonnegative')
    objective = SearchObjective(query=args.query, objective=args.objective, language=args.language,
                                channel=CHANNELS[args.cursor % len(CHANNELS)], limit=args.limit)
    load_env_file(names=DISCOVERY_ENV)
    ledger = Ledger.configured()
    storage = Storage()
    try:
        registry = ActivationRegistry(storage)
        directory = Directory(storage, registry_path=registry)
        tools = DomesticDiscovery.from_env(SourceGate(registry), directory)
        job = ledger.open_job(args.job_id, kind='maintenance')
        report = tools.rotate(objective, job, cursor=args.cursor, count=args.count)
        queue = OnboardingQueue(storage, registry, directory=directory)
        queued = queue.enqueue_domestic(report, args.category)
        print(json.dumps(dict(report=asdict(report), onboarding=[asdict(item) for item in queued],
                              next_cursor=args.cursor + args.count),
                         ensure_ascii=True, default=str))
        return 0 if any(a.status == 'ok' for a in report.attempts) else 1
    finally:
        storage.engine.dispose()
        ledger.storage.engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
