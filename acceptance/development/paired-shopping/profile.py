"""Exercise the pinned discovery connectors once on a separate development query."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
from uuid import uuid4

from app.buyer_flow import production_dependencies
from app.domestic_discovery import DISCOVERY_ENV, SearchObjective
from app.inference import load_env_file
from app.source_registry import DEFAULT_REGISTRY
from managed_scrape import Firecrawl


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('env_file', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    load_env_file(args.env_file, names=('OPENROUTER_API_KEY', *DISCOVERY_ENV))
    deps = production_dependencies(fixed_registry_path=DEFAULT_REGISTRY, load_worker_inference=False)
    if deps.ledger.project != 'techscout-december-2026':
        raise ValueError('Expected frozen campaign')
    tools = deps.research.workers.domestic_tools
    tools.fallback = Firecrawl.from_env(tools.gate, os.environ)
    job = deps.ledger.open_job('dev201-profile-' + str(uuid4()))
    channels = ('serpapi', 'brave', 'firecrawl', 'manufacturer', 'tavily')
    objective = SearchObjective('ASUS Zenbook S 13 OLED Lebanon', 'Independent tool setup', limit=4)
    report = tools.survey(objective, job, channels)
    from app.buyer_flow import _Flow
    from types import SimpleNamespace
    spend = _Flow._spend(SimpleNamespace(deps=deps, job=job, run_ref=SimpleNamespace(run_id=job.run_id)))
    attempts = {a.channel: asdict(a) for a in report.attempts}
    enabled = [name for name in channels if name in tools.connectors and
               (name != 'tavily' or attempts[name]['status'] == 'ok')]
    rates = {name: str(c.rate) for name, c in tools.connectors.items() if hasattr(c, 'rate')}
    scrape = dict(configured=bool(tools.fallback), status='unavailable')
    if tools.fallback:
        url = 'https://technodel.net/faq'
        try:
            page = tools.fallback.scrape(job, 'technodel', url)
            scrape.update(status=page.outcome.status, url=url, sha256=page.outcome.response_sha256)
        except Exception as exc:
            scrape.update(status='failed', reason=type(exc).__name__)
        rates['managed_scrape'] = str(tools.fallback.rate)
        spend = _Flow._spend(SimpleNamespace(deps=deps, job=job, run_ref=SimpleNamespace(run_id=job.run_id)))
    args.output.write_text(json.dumps(dict(channels=enabled, managed_scrape=bool(tools.fallback),
        query=objective.query, attempts=attempts, rates=rates, scrape=scrape, spend=spend,
        provider_preflight_passed=all(attempts[n]['status'] == 'ok' for n in channels[:-1]) and scrape['status'] == 'ok'),
        default=str, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
