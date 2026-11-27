"""Build the grader inputs for one held-out folder (used after the runs, never before).

    python eval/grading_args.py frozen  var/scout-eval/frozen  > grading-frozen.json
    python eval/grading_args.py confirm var/scout-eval/confirm > grading-confirm.json

Each case gets: the run report path, the expected class, the comparison flag and the
reference record as text, exactly as stored in the frozen manifest or confirmation grader.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.grade_scout import references  # noqa: E402


def main(argv: list[str]) -> int:
    kind, folder = argv[0], Path(argv[1]).resolve()
    cases = []
    for case_id, ref in references(kind).items():
        run = folder / f'{case_id}.json'
        cases.append(dict(id=case_id, run=str(run), expected_class=ref.get('expected_class'),
                          comparison_flag=bool(ref.get('comparison_flag')),
                          reference=json.dumps(ref, ensure_ascii=False, indent=1)[:6000]))
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(dict(cases=cases), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
