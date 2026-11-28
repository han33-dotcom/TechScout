"""Run the scout over a request file and save every report.

    python eval/run_scout.py acceptance/scout/requests/development.json --out var/scout-eval/dev [--ids D01 D03]
    python eval/run_scout.py <requests.json> --out <dir> --arm A B C D

Arms (buyer-flow designs, all on the same tools and the same deterministic checker):
    A  the single agent loop (app.scout.agent)
    B  the pipeline: one planner agent, code fan-out, parallel verifier agents (app.scout.pipeline)
    C  the team: an orchestrator agent directing a search agent and verifier agents
    D  arm B, searching the local full catalog (var/scout-catalog-full) instead of live shop search

Runs are sequential (one buyer ledger job each). With several arms, each request runs on every arm
back to back, in a rotating order, and reports go to <out>/<arm>/<id>.json. Each report is the full
RunResult JSON: the request, every stage or turn, model-call usage, the checked offers with their
evidence, timings and spend. A summary table is printed at the end.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.scout.__main__ import data_allowance, ledger_budget, make_provider  # noqa: E402
from app.scout.agent import RunConfig, ScoutAgent  # noqa: E402
from app.scout.catalog import ShopSearch  # noqa: E402
from app.scout.pipeline import ScoutPipeline  # noqa: E402
from app.scout.web import SafeWeb  # noqa: E402

LOCAL_CATALOG = ROOT / 'var' / 'scout-catalog-full'
CONDITION_GAP = 'new condition is not stated by the shop'


def load_requests(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding='utf-8'))
    if isinstance(raw, dict) and 'requests' in raw:
        return raw['requests']
    if isinstance(raw, dict) and 'cases' in raw:  # frozen cohort format
        return [dict(id=c['id'], text=c['input']['prompt'], mode=c['input'].get('mode'),
                     destination=c['input'].get('destination'), category=c['input'].get('category'))
                for c in raw['cases']]
    if isinstance(raw, list):  # confirmation-inputs format
        return [dict(id=c['id'], text=c['prompt'], mode=c.get('mode'), destination=c.get('destination'),
                     category=c.get('category')) for c in raw]
    raise ValueError('unknown request file format')


def build(arm: str, providers: dict, scope: str, weights=None):
    web = SafeWeb()
    search = ShopSearch(web, local_dir=LOCAL_CATALOG if arm == 'D' else None, weights=weights)
    if arm == 'A':
        agent = ScoutAgent(providers['loop'], web=web, search=search, budget_factory=ledger_budget,
                           config=RunConfig(), scope=scope)
        agent.data_bytes = data_allowance(providers['loop'])
        return agent
    return ScoutPipeline(providers['planner'], providers['verifier'], orchestrator='llm' if arm == 'C' else 'code',
                         web=web, search=search, budget_factory=pipeline_budget)


def pipeline_budget(run_id: str):
    """The buyer ledger job with 30 s reservations, so a call that waited for the ledger can still settle."""
    from app.accounting import Ledger
    from app.model_provider import JobBudget
    job = Ledger.configured().open_job(run_id, 'buyer')
    return JobBudget(job, seconds=30), job


def summary_row(arm: str, req: dict, result) -> dict:
    shown = [o for o in result.offers if o.tier in ('best', 'alternative')]
    best = [o for o in shown if o.tier == 'best']
    only_condition = [o for o in best if not o.complete and o.identity == 'exact' and o.in_stock is True
                      and o.price is not None and o.gaps == [CONDITION_GAP]]
    statuses = [c['status'] for c in result.model_calls]
    return dict(arm=arm, id=req['id'], status=result.status, seconds=result.seconds, calls=len(result.model_calls),
                failed_calls=len([s for s in statuses if s != 'completed']),
                usd=result.spend['settled_usd'], uncertain=result.spend['uncertain_usd'],
                shop_requests=result.retailer_requests, shops=len({o.shop_id for o in shown}), best=len(best),
                totals=len([o for o in shown if o.total is not None]),
                complete=len([o for o in best if o.complete]), only_condition_open=len(only_condition),
                fallback=result.fallback,
                top=(f'{shown[0].shop} {shown[0].currency} {shown[0].price}'
                     + (f' (total {shown[0].total})' if shown[0].total is not None else '')) if shown else '-')


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument('requests')
    p.add_argument('--out', required=True)
    p.add_argument('--ids', nargs='*')
    p.add_argument('--gap', type=float, default=3.0, help='seconds between runs')
    p.add_argument('--arm', nargs='*', default=['A'], choices=['A', 'B', 'C', 'D'])
    p.add_argument('--scope', default='listed', choices=['listed', 'total'],
                   help='listed = buyer contract v2, the listed price before VAT and delivery (the default; arms B, '
                        'C and D always use it). total = the contract v1 rule, for reproducing earlier results')
    args = p.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    providers = dict(loop=make_provider())
    if set(args.arm) - {'A'}:
        # Small allowances keep each call's ledger reservation small; 26 s leaves room for the ledger steps.
        providers.update(planner=make_provider(20_000, 1_200, 22), verifier=make_provider(28_000, 1_800, 26))
    warm = ShopSearch(SafeWeb(), local_dir=LOCAL_CATALOG if 'D' in args.arm else None)
    print(json.dumps(dict(arms=args.arm, scope=args.scope, indexes_loaded=warm.warm())), flush=True)
    rows = []
    wanted = [r for r in load_requests(Path(args.requests)) if not args.ids or r['id'] in args.ids]
    for n, req in enumerate(wanted):
        order = args.arm[n % len(args.arm):] + args.arm[:n % len(args.arm)]
        for arm in order:
            folder = out / arm if len(args.arm) > 1 else out
            folder.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime('%Y%m%dT%H%M%S', time.gmtime())
            runner = build(arm, providers, args.scope, warm.weights)
            request = dict(req, run_id=f"scout-{req['id'].lower()}-{arm.lower()}-{stamp}")
            try:
                result = runner.run(request)
            except Exception as exc:  # noqa: BLE001 - one crashed run is recorded, the rest still run
                row = dict(arm=arm, id=req['id'], status='crashed', error=f'{type(exc).__name__}: {exc}'[:300])
                rows.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
                continue
            report = dict(result.public(), arm=arm, scope='listed' if arm != 'A' else args.scope)
            (folder / f"{req['id']}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str),
                                                      encoding='utf-8')
            row = summary_row(arm, req, result)
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            time.sleep(args.gap)
    (out / 'summary.json').write_text(json.dumps(rows, indent=1), encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main())
