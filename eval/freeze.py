"""Check or record the content hashes of the frozen evaluation protocol (issue #78).

Run from the repository root:

    python eval/freeze.py                          check every recorded hash (exit 1 on a mismatch)
    python eval/freeze.py --bump 1.1.0 "reason"    re-record after a deliberate protocol change
    python eval/freeze.py --pre-run                record warm.json and run-freeze.json once, before held-out runs
    python eval/freeze.py --pre-run --start-now    use the launch time when the first block is ready

Hashes are SHA-256 over each file's bytes with CRLF line endings normalized to LF, so a
Windows checkout and the Linux CI compute the same values. The combined hash covers the
sorted list of 'path  hash' lines.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

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


def _require_filled(value, template, field):
    if isinstance(template, dict):
        if not isinstance(value, dict):
            raise ValueError(f'{field} must be an object')
        for key, expected in template.items():
            if key not in {'instructions', 'issue', 'required_attestation_values'}:
                _require_filled(value.get(key), expected, f'{field}.{key}')
    elif isinstance(template, list):
        if not isinstance(value, list):
            raise ValueError(f'{field} must be a list')
        if template:
            for index, item in enumerate(value):
                _require_filled(item, template[0], f'{field}[{index}]')
    elif value is None or isinstance(value, str) and not value.strip():
        if not field.endswith('.repetition_reduction_reason'):
            raise ValueError(f'Missing pre-run value for {field}')


def pre_run(root: Path = ROOT, now: str | None = None, *, start_now: bool = False) -> dict:
    """Record the warm-corpus manifest and run settings once, before the first held-out run."""
    record = load(root)
    if record.get('pre_run') is not None:
        raise ValueError('The pre-run freeze is already recorded')
    changes = problems(root)
    if changes:
        raise ValueError(changes[0])
    recorded_at = now or datetime.now(timezone.utc).isoformat(timespec='seconds')
    launch_settings = None
    for name in PRE_RUN_FILES:
        if not (root / name).is_file():
            raise ValueError(f'{name} does not exist yet')
        data = json.loads((root / name).read_text(encoding='utf-8'))
        if data.get('state') != 'frozen':
            raise ValueError(f'{name} must have state "frozen"')
        if start_now and name == 'eval/run-freeze.json':
            schedule = data.get('schedule')
            if isinstance(schedule, dict) and schedule.get('first_block_planned_at') is None:
                schedule['first_block_planned_at'] = recorded_at
                launch_settings = data
        template = json.loads((root / name.replace('.json', '.template.json')).read_bytes())
        _require_filled(data, template, name)
        if name.endswith('/warm.json'):
            for key, expected in template['required_attestation_values'].items():
                if data['attestations'][key] is not expected:
                    raise ValueError(f'Warm corpus attestation does not hold: {key}')
        else:
            if data['protocol_version'] != record['version']:
                raise ValueError('Run settings belong to a different protocol version')
            start = data['schedule']['first_block_planned_at']
            if not isinstance(start, str) or datetime.fromisoformat(start).utcoffset() is None:
                raise ValueError('The first paired block requires a time with a UTC offset')
            for role in ('operators', 'verifiers'):
                members = data['schedule'][role]
                if not members or not all(isinstance(member, str) and member.strip() for member in members):
                    raise ValueError(f'Record the scheduled {role} before the freeze')
    if launch_settings is not None:
        path = root / 'eval/run-freeze.json'
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            json.dump(launch_settings, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        try:
            Path(stream.name).replace(path)
        finally:
            Path(stream.name).unlink(missing_ok=True)
    files = hashes(PRE_RUN_FILES, root)
    record['pre_run'] = {'recorded_at': recorded_at,
                         'protocol_version': record['version'], 'files': files, 'combined_sha256': combined(files)}
    _write(record, root)
    return record


def main(argv: list[str]) -> int:
    try:
        if argv[:1] == ['--bump'] and len(argv) == 3:
            print(f"Recorded protocol {bump(argv[1], argv[2])['version']}")
            return 0
        if argv in (['--pre-run'], ['--pre-run', '--start-now']):
            pre_run(start_now='--start-now' in argv)
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
