"""Run the scout agent over a request file and save every report.

    python eval/run_scout.py acceptance/scout/requests/development.json --out var/scout-eval/dev [--ids D01 D03]

Runs are sequential (one buyer ledger job each). Each report is the full RunResult JSON:
the request, every turn's notes and tool calls, model-call usage, the checked offers with
their evidence, timings and spend. A summary table is printed at the end.
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


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument('requests')
    p.add_argument('--out', required=True)
    p.add_argument('--ids', nargs='*')
    p.add_argument('--gap', type=float, default=3.0, help='seconds between runs')
    args = p.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    provider = make_provider()
    rows = []
    for req in load_requests(Path(args.requests)):
        if args.ids and req['id'] not in args.ids:
            continue
        stamp = time.strftime('%Y%m%dT%H%M%S', time.gmtime())
        agent = ScoutAgent(provider, budget_factory=ledger_budget, config=RunConfig())
        agent.data_bytes = data_allowance(provider)
        request = dict(req, run_id=f"scout-{req['id'].lower()}-{stamp}")
        result = agent.run(request)
        (out / f"{req['id']}.json").write_text(json.dumps(result.public(), ensure_ascii=False, indent=1,
                                                         default=str), encoding='utf-8')
        best = [o for o in result.offers if o.tier in ('best', 'alternative')]
        row = dict(id=req['id'], status=result.status, seconds=result.seconds, calls=len(result.model_calls),
                   usd=result.spend['settled_usd'], uncertain=result.spend['uncertain_usd'],
                   shops=len({o.shop_id for o in best}), best=len([o for o in best if o.tier == 'best']),
                   totals=len([o for o in best if o.total is not None]),
                   complete=len([o for o in best if o.complete]), fallback=result.fallback,
                   top=(f'{best[0].shop} {best[0].currency} {best[0].price}'
                        + (f' (total {best[0].total})' if best[0].total is not None else '')) if best else '-')
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        time.sleep(args.gap)
    (out / 'summary.json').write_text(json.dumps(rows, indent=1), encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main())
