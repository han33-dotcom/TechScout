"""Back up a stopped local app/worker or restore to fresh Compose volumes."""
import argparse
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
DB_USER = '"$POSTGRES_USER"'
DB_NAME = '"$POSTGRES_DB"'
HAS_APP_TABLES = ("SELECT EXISTS (SELECT FROM pg_tables "
                  "WHERE schemaname = 'public')")
EMPTY_EVIDENCE = 'test -z "$(ls -A "$EVIDENCE_DIR")"'


def compose(project, *args, **options):
    command = ['docker', 'compose']
    if project:
        command += ['--project-name', project]
    return subprocess.run([*command, *args], cwd=ROOT, check=True, **options)


def database(project, command, **options):
    return compose(project, 'exec', '-T', 'db', 'sh', '-ec', command, **options)


def evidence(project, command, **options):
    return compose(project, 'run', '--rm', '--no-deps', '-T',
                   'worker', 'sh', '-ec', command, **options)


def paths(archive):
    return archive / 'database.dump', archive / 'evidence.tar.gz'


def require_stopped_writers(project):
    running = compose(project, 'ps', '--status', 'running', '--services',
                      'app', 'worker', 'migrate', capture_output=True, text=True).stdout.strip()
    if running:
        raise SystemExit('Stop the API and worker and wait for migrations before copying data.')


def backup(project, archive):
    require_stopped_writers(project)
    archive.mkdir(mode=0o700)
    dump_path, evidence_path = paths(archive)
    try:
        with dump_path.open('wb') as dump:
            database(project, f'pg_dump -Fc -U {DB_USER} -d {DB_NAME}', stdout=dump)
        with evidence_path.open('wb') as snapshots:
            evidence(project, 'tar -czf - -C "$EVIDENCE_DIR" .', stdout=snapshots)
    except BaseException:
        dump_path.unlink(missing_ok=True)
        evidence_path.unlink(missing_ok=True)
        archive.rmdir()
        raise


def restore(project, archive):
    dump_path, evidence_path = paths(archive)
    with dump_path.open('rb') as dump, evidence_path.open('rb') as snapshots:
        require_stopped_writers(project)
        initialized = database(project, f'psql -XAt -U {DB_USER} -d {DB_NAME} -c "{HAS_APP_TABLES}"',
                               capture_output=True, text=True).stdout.strip()
        if initialized != 'f':
            raise SystemExit('Restore requires an empty database. Start only db in a fresh project.')
        evidence(project, EMPTY_EVIDENCE)
        # Validate the archive before applying either half. Run the DB restore as one transaction.
        database(project, 'pg_restore --list >/dev/null', stdin=dump)
        dump.seek(0)
        evidence(project, 'tar -tzf - >/dev/null', stdin=snapshots)
        snapshots.seek(0)
        database(project, f'pg_restore --single-transaction --exit-on-error --no-owner '
                 f'--no-privileges -U {DB_USER} -d {DB_NAME}', stdin=dump)
        evidence(project, 'umask 077; tar --keep-old-files -xzf - -C "$EVIDENCE_DIR"',
                 stdin=snapshots)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('backup', 'restore'))
    parser.add_argument('directory', type=Path)
    parser.add_argument('--project', default=os.environ.get('COMPOSE_PROJECT_NAME'))
    args = parser.parse_args()
    operation = backup if args.operation == 'backup' else restore
    operation(args.project, args.directory.resolve())


if __name__ == '__main__':
    main()
