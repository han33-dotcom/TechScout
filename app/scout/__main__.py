"""Run one scout search from the command line.

    python -m app.scout "Find new black Sony WH-CH520 headphones delivered to Batroun" --destination Batroun

Live runs need OPENROUTER_API_KEY (repository .env), var/inference.json and the shared
ledger settings (TECHSCOUT_ACCOUNTING_URL / _PROJECT, loaded by Use-TechScoutLive.ps1).
Every model call is reserved and settled in the shared ledger. The JSON report is written
to var/scout-runs/<run_id>.json.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from app.scout.agent import PROMPT, PROMPT_VERSION, RunConfig, ScoutAgent, Step

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / 'var' / 'scout-runs'
FRAMING = 4096


def make_provider(max_input: int = 96_000, max_output: int = 5_000, timeout: int = 25):
    from app.inference import load_env_file, load_inference_config
    from app.openrouter import OpenRouterConfig, OpenRouterProvider
    load_env_file(names=('OPENROUTER_API_KEY',))
    cfg = load_inference_config()
    settings = OpenRouterConfig(
        enabled=True, purpose='buyer', endpoint=cfg.endpoint, routes=(cfg.route,),
        data_collection=cfg.data_collection, zdr=cfg.zdr, effort=cfg.effort,
        max_input_tokens=max_input, max_output_tokens=max_output, timeout_seconds=timeout,
        prompt_version=PROMPT_VERSION, pricing_checked_on=cfg.pricing_checked_on)
    return OpenRouterProvider(settings)


def data_allowance(provider) -> int:
    from app.model_provider import _schema
    fixed = len(PROMPT.encode('utf-8')) + len(json.dumps(_schema(Step)).encode('utf-8')) + FRAMING + 3000
    # The data travels as a JSON string inside the request JSON, so its quotes are escaped (~+12%).
    return max(20_000, int((provider.settings.max_input_tokens - fixed) / 1.15))


def ledger_budget(run_id: str):
    from app.accounting import Ledger
    from app.model_provider import JobBudget
    ledger = Ledger.configured()
    job = ledger.open_job(run_id, 'buyer')
    return JobBudget(job, seconds=25), job


def render(result) -> str:
    out = [f'Status: {result.status}   ({result.seconds}s, {len(result.model_calls)} model calls, '
           f'{result.retailer_requests} shop requests, USD {result.spend["settled_usd"]} settled'
           + (f', USD {result.spend["uncertain_usd"]} uncertain' if result.spend['uncertain_usd'] != '0' else '')
           + ')', '', result.message, '']
    if result.question:
        out += [f'Question: {result.question}', '']
    for o in result.offers:
        price = f'{o.currency or ""} {o.price}' if o.price is not None else 'no price'
        total = f' | total USD {o.total}' if o.total is not None else ''
        out.append(f'[{o.tier}{" COMPLETE" if o.complete else ""}] {o.shop}: {o.title} | {o.variant} | '
                   f'{price}{total} | stock {o.in_stock} | condition {o.condition} | identity {o.identity}')
        out.append(f'    {o.url}')
        if o.gaps:
            out.append('    not confirmed: ' + '; '.join(o.gaps))
        if o.downgrades:
            out.append('    checks: ' + '; '.join(o.downgrades))
    for w in result.warnings:
        out.append(f'WARNING: {w}')
    return '\n'.join(out)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog='python -m app.scout')
    p.add_argument('text')
    p.add_argument('--mode', choices=('delivery', 'pickup'), default='delivery')
    p.add_argument('--destination', default=None)
    p.add_argument('--category', default=None)
    p.add_argument('--run-id', default=None)
    p.add_argument('--no-ledger', action='store_true', help='development only: skip the shared ledger')
    p.add_argument('--turns', type=int, default=7)
    args = p.parse_args(argv)
    provider = make_provider()
    agent = ScoutAgent(provider, budget_factory=None if args.no_ledger else ledger_budget,
                       config=RunConfig(max_turns=args.turns))
    agent.data_bytes = data_allowance(provider)
    request = dict(text=args.text, mode=args.mode, destination=args.destination, category=args.category,
                   run_id=args.run_id)
    result = agent.run(request)
    RUNS.mkdir(parents=True, exist_ok=True)
    path = RUNS / f'{result.run_id}.json'
    path.write_text(json.dumps(result.public(), ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    print(render(result))
    print(f'\nReport: {path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
