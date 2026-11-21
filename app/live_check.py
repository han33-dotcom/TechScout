"""Dry-run preflight and evidence summary for the first live buyer run (#75, docs/live-flow.md).

    python -m app.live_check                 # check every precondition; nothing is sent
    python -m app.live_check --check-ledger  # also read the shared ledger (no reservation)
    python -m app.live_check summarize var/traces/<run>-r1.json

The check never sends a model or retailer request and never prints a key: it reports only
whether OPENROUTER_API_KEY is present. The live run itself starts from the browser, under a
person's control, after every check passes.
"""
import argparse
import json
import os
from pathlib import Path
import sys

from app.inference import (
    FINAL_RESERVE_USD, KEY_VARIABLE, RESEARCH_SPEND_USD, ROOT, InferenceUnavailable, config_path,
    load_env_file, load_inference_config,
)

SOURCE_ID = 'class_cellulars'


def _line(ok, label, detail=''):
    return ('OK      ' if ok else 'MISSING ') + label + (f': {detail}' if detail else '')


def check(*, environ=None, check_ledger=False, root=ROOT) -> tuple[bool, list[str]]:
    """Every precondition for one bounded live run, as printable lines; True when all pass."""
    environ = dict(os.environ if environ is None else environ)
    lines, ready = [], True

    def add(ok, label, detail=''):
        nonlocal ready
        ready = ready and ok
        lines.append(_line(ok, label, detail))

    # 1. Model route and prices (separate from the frozen evaluation files; see #80).
    path = config_path(environ)
    try:
        config = load_inference_config(path, environ=environ)
    except InferenceUnavailable as exc:
        config = None
        add(False, 'inference configuration', f'{exc} [looked for {path}]')
    else:
        roles, writer = config.reservation('roles'), config.reservation('writer')
        add(True, 'inference configuration', f'{path}')
        lines.append(f'        model {config.route.model} via serving provider {config.route.provider}; '
                     f'prices checked {config.pricing_checked_on}; fallback disabled')
        lines.append(f'        one role call reserves USD {roles}; one writer call reserves USD {writer}')
        lines.append(f'        at most {int(RESEARCH_SPEND_USD // roles)} role calls fit the USD {RESEARCH_SPEND_USD} '
                     f'research allowance; the writer fits the USD {FINAL_RESERVE_USD} final reserve')

    # 2. Backend key: presence only. The repository .env is read the way the worker reads it.
    probe = dict(environ)
    load_env_file(root / '.env', environ=probe)
    add(bool((probe.get(KEY_VARIABLE) or '').strip()), KEY_VARIABLE,
        'present in the backend environment or repository .env (value not shown)'
        if (probe.get(KEY_VARIABLE) or '').strip() else 'add it to the repository-root .env yourself')
    leaked = [str(p.relative_to(root)) for p in (root / 'frontend').glob('.env*')
              if p.is_file() and 'OPENROUTER' in p.read_text(encoding='utf-8', errors='ignore').upper()]
    add(not leaked, 'key kept out of the browser build', ', '.join(leaked) or 'no frontend .env names it')
    lines.append('        OPENAI_API_KEY is ' + ('set but unused' if environ.get('OPENAI_API_KEY') else 'unset')
                 + ': the buyer worker never builds the direct OpenAI provider')

    # 3. Shared accounting ledger (today on Hasan's PC).
    url, project = environ.get('TECHSCOUT_ACCOUNTING_URL', '').strip(), environ.get(
        'TECHSCOUT_ACCOUNTING_PROJECT', '').strip()
    add(bool(url and project), 'accounting variables',
        'TECHSCOUT_ACCOUNTING_URL and TECHSCOUT_ACCOUNTING_PROJECT set' if url and project
        else 'load them with scripts/Use-LocalAccounting.ps1 on the ledger machine')
    if check_ledger and url and project:
        try:
            from app.accounting import Ledger
            from app.storage import Storage
            state = Ledger(Storage(url), project).snapshot()
            used = (state['scopes'].get('project') or [0, 0, 0, '0', 0])[3]
            add(not state['blocked'], 'accounting ledger reachable',
                f"project {state['project']}; USD {used} of 500 used; "
                + ('BLOCKED for reconciliation' if state['blocked'] else 'not blocked'))
        except Exception as exc:  # noqa: BLE001 - reported by type only
            add(False, 'accounting ledger reachable', type(exc).__name__)

    # 4. Source registry and retailer directory.
    try:
        from app.source_registry import DEFAULT_REGISTRY, get_active_source
        get_active_source(SOURCE_ID, DEFAULT_REGISTRY)
        add(True, 'source registry', f'{SOURCE_ID} is active')
    except (PermissionError, LookupError, ValueError) as exc:
        add(False, 'source registry', f'{SOURCE_ID}: {type(exc).__name__}')
    if environ.get('DATABASE_URL') or environ.get('TECHSCOUT_DB_HOST'):
        try:
            from app.directory import Directory
            from app.storage import Storage
            storage = Storage()
            storage.ping()
            found = Directory(storage).get(SOURCE_ID) is not None
            add(found, 'retailer directory', f'{SOURCE_ID} imported' if found
                else 'run: python -m app.directory import-seeds')
        except Exception as exc:  # noqa: BLE001
            add(False, 'application database', f'{type(exc).__name__}; run alembic upgrade head')
    else:
        add(False, 'application database', 'export DATABASE_URL for the API and worker (same database)')
    return ready, lines


def summarize(path) -> dict:
    """The evidence a finished run's trace supports, for the issue comment."""
    trace = json.loads(Path(path).read_text(encoding='utf-8'))
    calls = trace.get('model_calls') or []
    source = trace.get('source') or {}
    verify = trace.get('verify') or {}
    return dict(
        run=trace.get('run'), status=trace.get('status'), stop_reason=trace.get('stop_reason'),
        started_at=trace.get('started_at'), latency_ms=trace.get('latency_ms'),
        stopped_by=trace.get('stopped_by'), versions=trace.get('versions'),
        discovery=dict(mode=source.get('mode'), fetches=len(source.get('fetches') or []),
                       offers=source.get('offers'), errors=source.get('errors')),
        final_recheck=dict(finalists=len(verify.get('finalists') or []), outcomes=verify.get('outcomes'),
                           fetches=len(verify.get('fetches') or [])),
        model_calls=[dict(role=c.get('role'), status=c.get('status'), validated=c.get('validated'),
                          requested_model=(c.get('usage') or {}).get('requested_model'),
                          reported_model=(c.get('usage') or {}).get('actual_model'),
                          serving_provider=(c.get('usage') or {}).get('serving_provider'),
                          tokens=(c.get('usage') or {}).get('tokens'),
                          latency_ms=(c.get('usage') or {}).get('latency_ms'),
                          spend=(c.get('usage') or {}).get('spend'),
                          actual_usd=(c.get('usage') or {}).get('actual_usd'),
                          reserved_usd=(c.get('usage') or {}).get('reserved_usd'),
                          missing_metadata=(c.get('usage') or {}).get('metadata_unavailable'))
                     for c in calls],
        validated_responses=sum(1 for c in calls if c.get('validated')),
        spend=trace.get('spend'), writer=trace.get('writer'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--check-ledger', action='store_true', help='also read the shared ledger (no reservation)')
    sub = parser.add_subparsers(dest='command')
    summary = sub.add_parser('summarize', help="summarize a finished run's sanitized trace")
    summary.add_argument('trace')
    args = parser.parse_args(argv)
    if args.command == 'summarize':
        print(json.dumps(summarize(args.trace), indent=2, sort_keys=True, default=str))
        return 0
    ready, lines = check(check_ledger=args.check_ledger)
    print('\n'.join(lines))
    print('\nReady for one bounded live run.' if ready else '\nNot ready: fix the MISSING items first. Nothing was sent.')
    return 0 if ready else 1


if __name__ == '__main__':
    sys.exit(main())
