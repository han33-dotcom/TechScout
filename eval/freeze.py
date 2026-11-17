"""Check or record the content hashes of the frozen evaluation protocol (issue #78).

Run from the repository root:

    python eval/freeze.py                          check every recorded hash (exit 1 on a mismatch)
    python eval/freeze.py --bump 1.1.0 "reason"    re-record after a deliberate protocol change
    python eval/freeze.py --pre-run                record warm.json and run-freeze.json once, before held-out runs

Hashes are SHA-256 over each file's bytes with CRLF line endings normalized to LF, so a
Windows checkout and the Linux CI compute the same values. The combined hash covers the
sorted list of 'path  hash' lines.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
RECORD = 'eval/freeze.json'
PRE_RUN_FILES = ('eval/corpus/warm.json', 'eval/run-freeze.json')


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def combined(hashes: dict[str, str]) -> str:
    lines = ''.join(f'{name}  {hashes[name]}\n' for name in sorted(hashes))
    return hashlib.sha256(lines.encode('utf-8')).hexdigest()


def hashes(names, root: Path = ROOT) -> dict[str, str]:
    return {name: digest(root / name) for name in names}


def load(root: Path = ROOT) -> dict:
    return json.loads((root / RECORD).read_text(encoding='utf-8'))


def _section_problems(label: str, section: dict, root: Path) -> list[str]:
    found = []
    for name, expected in sorted(section['files'].items()):
        path = root / name
        if not path.is_file():
            found.append(f'{label}: {name} is missing')
        elif digest(path) != expected:
            found.append(f'{label}: {name} changed since it was frozen')
    if combined(section['files']) != section['combined_sha256']:
        found.append(f'{label}: the combined hash does not match the recorded file hashes')
    return found


def problems(root: Path = ROOT) -> list[str]:
    """Every difference between the files on disk and the recorded freeze."""
    record = load(root)
    found = _section_problems(f"protocol {record['version']}", record, root)
    if record.get('pre_run') is not None:
        found += _section_problems('pre-run freeze', record['pre_run'], root)
    return found


def _version(text: str) -> tuple[int, ...]:
    parts = text.split('.')
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError(f'Use a MAJOR.MINOR.PATCH version, not {text!r}')
    return tuple(int(part) for part in parts)


def _write(record: dict, root: Path) -> None:
    (root / RECORD).write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def bump(version: str, reason: str, root: Path = ROOT, today: str | None = None) -> dict:
    """Re-record the protocol hashes under a higher version with a changelog reason."""
    record = load(root)
    if _version(version) <= _version(record['version']):
        raise ValueError(f"Version {version} must be higher than {record['version']}")
    if not reason.strip():
        raise ValueError('Give the reason for the change')
    if record.get('pre_run') is not None:
        raise ValueError('Held-out runs are frozen; publish a separately labelled post-freeze protocol instead')
    record['files'] = hashes(record['files'], root)
    record['combined_sha256'] = combined(record['files'])
    record['version'] = version
    record['changelog'].append({'version': version, 'date': today or datetime.now(timezone.utc).date().isoformat(),
                                'change': reason.strip()})
    _write(record, root)
    return record


def pre_run(root: Path = ROOT, now: str | None = None) -> dict:
    """Record the warm-corpus manifest and run settings once, before the first held-out run."""
    record = load(root)
    if record.get('pre_run') is not None:
        raise ValueError('The pre-run freeze is already recorded')
    for name in PRE_RUN_FILES:
        if not (root / name).is_file():
            raise ValueError(f'{name} does not exist yet')
        data = json.loads((root / name).read_text(encoding='utf-8'))
        if data.get('state') != 'frozen':
            raise ValueError(f'{name} must have state "frozen"')
    files = hashes(PRE_RUN_FILES, root)
    record['pre_run'] = {'recorded_at': now or datetime.now(timezone.utc).isoformat(timespec='seconds'),
                         'protocol_version': record['version'], 'files': files, 'combined_sha256': combined(files)}
    _write(record, root)
    return record


def main(argv: list[str]) -> int:
    try:
        if argv[:1] == ['--bump'] and len(argv) == 3:
            print(f"Recorded protocol {bump(argv[1], argv[2])['version']}")
            return 0
        if argv == ['--pre-run']:
            pre_run()
            print('Recorded the pre-run freeze')
            return 0
        if argv:
            print(__doc__, file=sys.stderr)
            return 2
    except ValueError as error:
        print(f'Refused: {error}', file=sys.stderr)
        return 1
    found = problems()
    for item in found:
        print(item, file=sys.stderr)
    if not found:
        print(f"Evaluation protocol {load()['version']} matches its frozen hashes")
    return 1 if found else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
