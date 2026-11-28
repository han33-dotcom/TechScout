"""Put each scout run next to its independent reference so a person can score it.

    python eval/grade_scout.py frozen  var/scout-eval/frozen    > side-by-side.txt
    python eval/grade_scout.py confirm var/scout-eval/confirm   > side-by-side.txt

The script does not decide credit. It prints, for every case: the buyer's request, the
expected class and outcome from the frozen manifest (or the confirmation grader), the
reference offers, and what the scout showed (status, message, every checked offer with its
tier, price, delivered total, condition, gaps and link). The grader records decisions and
reasons in a scorecard JSON using the frozen protocol (docs/buyer-correctness-acceptance.md).
Run it only after the candidate's runs are complete: it reads the reference answers.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / 'acceptance' / 'buyer-v1' / 'freeze-v1-2026-11-25.json'
CONFIRM = ROOT / 'acceptance' / 'architecture-v2' / 'grader' / 'confirmation-references.json'


def references(kind: str) -> dict:
    if kind == 'frozen':
        data = json.loads(FROZEN.read_text(encoding='utf-8'))
        return {c['id']: c for c in data['cases']}
    data = json.loads(CONFIRM.read_text(encoding='utf-8'))
    observations = data.get('observations') or {}
    inputs = {c['id']: c for c in json.loads(
        (ROOT / 'acceptance' / 'architecture-v2' / 'confirmation-inputs.json').read_text(encoding='utf-8'))}
    out = {}
    for c in data.get('cases', []):
        # The confirmation grader names a reference status instead of a class, and keeps the
        # evidence records separately; attach both so a grader sees the whole reference.
        out[c['id']] = dict(c, expected_class=c.get('status'), expected_buyer_outcome=c.get('expected'),
                            input=inputs.get(c['id'], {}),
                            evidence_records={k: observations.get(k) for k in c.get('evidence', [])})
    return out


def show_run(run: dict) -> list[str]:
    lines = [f"  scout status: {run['status']}  ({run['seconds']} s, {len(run['model_calls'])} model calls, "
             f"USD {run['spend']['settled_usd']}, fallback={run['fallback']})",
             '  message: ' + run['message'].replace('\n', ' / ')[:900]]
    if run.get('question'):
        lines.append(f"  question: {run['question']}")
    for o in run['offers']:
        lines.append(f"  [{o['tier']}{' COMPLETE' if o['complete'] else ''}] {o['shop']} | {o['title'][:70]} | "
                     f"{o['variant'][:40]} | {o['currency']} {o['price']} | total {o['total']} | stock {o['in_stock']} | "
                     f"cond {o['condition']} | id {o['identity']}")
        lines.append(f"      {o['url']}")
        if o['gaps']:
            lines.append('      gaps: ' + '; '.join(o['gaps']))
        if o['downgrades']:
            lines.append('      checks: ' + '; '.join(o['downgrades']))
    for w in run.get('warnings', []):
        lines.append(f'  WARNING: {w}')
    return lines


def main(argv: list[str]) -> int:
    kind, folder = argv[0], Path(argv[1])
    refs = references(kind)
    sys.stdout.reconfigure(encoding='utf-8')
    for case_id, ref in refs.items():
        path = folder / f'{case_id}.json'
        print('=' * 100)
        prompt = (ref.get('input') or {}).get('prompt') or ref.get('prompt') or ''
        print(f"{case_id}  class={ref.get('expected_class')}  pair={ref.get('comparison_flag')}  {prompt}")
        if ref.get('expected_buyer_outcome'):
            print('  expected: ' + ref['expected_buyer_outcome'])
        if ref.get('variant'):
            print('  variant: ' + str(ref['variant']))
        for o in ref.get('offers') or []:
            print(f"  ref offer: {o.get('seller')} | {o.get('url')} | price {o.get('price_usd')} | "
                  f"delivery {o.get('delivery_usd')} | payable {o.get('payable_usd')}")
        if not path.is_file():
            print('  scout: NO RUN')
            continue
        print('\n'.join(show_run(json.loads(path.read_text(encoding='utf-8')))))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
