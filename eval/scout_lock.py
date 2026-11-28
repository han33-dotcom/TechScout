"""Write the scout candidate's run lock before any held-out request is run.

    python eval/scout_lock.py > acceptance/scout/run-lock.json

Records the code commit, the model route (no key), prompt versions and limits, the shop
directory hash, the catalog index and shop-terms dates, and the SHA-256 of the held-out input
files. Commit it, then run the held-out sets on that commit without changing anything.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.scout import agent, terms  # noqa: E402
from app.scout.catalog import CACHE_DIR, SEARCH_SECONDS  # noqa: E402

INPUTS = ['acceptance/architecture-v2/confirmation-inputs.json', 'acceptance/architecture-v2/primary-inputs.json']


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def main() -> int:
    inference = json.loads((ROOT / 'var' / 'inference.json').read_text(encoding='utf-8-sig'))['inference']
    route = inference['route']
    indexes = {}
    for path in sorted(CACHE_DIR.glob('*.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        indexes[path.stem] = dict(fetched_at=data.get('fetched_at'), rows=len(data.get('rows', [])))
    shop_terms = {}
    for path in sorted(terms.TERMS_DIR.glob('*.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        shop_terms[path.stem] = dict(fetched_at=data.get('fetched_at'), status=data.get('status'),
                                     delivery_rules=len(data.get('delivery') or []), vat=bool(data.get('vat')),
                                     addresses=len(data.get('addresses') or []))
    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(['git', 'status', '--porcelain', '--', 'app', 'config', 'eval'], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    lock = dict(
        locked_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
        candidate_commit=commit, working_tree_clean_for_app_config_eval=not dirty,
        model=dict(model=route['model'], serving_provider=route['provider'], effort=inference.get('effort'),
                   rates=route['rates'], pricing_checked_on=inference.get('pricing_checked_on'),
                   max_input_bytes=96000, max_output_tokens=5000, timeout_seconds=25, fallback='disabled'),
        prompts=dict(agent=agent.PROMPT_VERSION, terms=terms.PROMPT_VERSION),
        limits=dict(max_turns=agent.RunConfig.max_turns, research_until_s=agent.RunConfig.research_until_s,
                    final_by_s=agent.RunConfig.final_by_s, search_seconds=SEARCH_SECONDS,
                    ledger='shared buyer job per run (120 s window, USD 1 cap)'),
        shop_directory=dict(path='config/scout-shops.json', sha256=sha(ROOT / 'config' / 'scout-shops.json')),
        catalog_indexes=indexes, shop_terms=shop_terms,
        inputs={p: sha(ROOT / p) for p in INPUTS},
        order=['confirmation (C01, C02, C03, C07, C05, C06)', 'frozen 20 in primary-inputs.json order'],
        rule='Run once each, sequentially, on this commit; no code, prompt, directory or index change until all '
             'held-out runs and grading are complete. Reference answers are read only after the runs.')
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(lock, indent=1, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
