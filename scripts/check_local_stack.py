"""Run the offline Compose startup, persistence and clean-restore checks."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.error import HTTPError
from urllib.request import urlopen
from uuid import uuid4

from local_recovery import ROOT, backup, compose, database, restore

FIXTURE = ROOT / 'scripts' / 'recovery_fixture.py'


def fixture(project, *args):
    with FIXTURE.open('rb') as code:
        compose(project, 'exec', '-T', 'app', 'python', '-', *args, stdin=code)


def verify_http(project):
    address = compose(project, 'port', 'app', '8000', capture_output=True, text=True).stdout.strip()
    base = f'http://{address}'
    with urlopen(base + '/health', timeout=5) as response:
        assert json.load(response) == {'status': 'ok', 'database': 'ok'}
    with urlopen(base + '/sources', timeout=5) as response:
        assert any(source['id'] == 'beytech' for source in json.load(response))
    with urlopen(base + '/', timeout=5) as response:
        html = response.read().decode()
        assert '<title>TechScout</title>' in html
    asset = re.search(r'src="(/assets/[^"]+\.js)"', html).group(1)
    with urlopen(base + asset, timeout=5) as response:
        assert 'javascript' in response.headers.get_content_type()
        assert b'/health' in response.read()
    for path in ('/api/missing', '/evidence/missing'):
        try:
            with urlopen(base + path, timeout=5):
                raise AssertionError(f'Unexpected response for {path}')
        except HTTPError as error:
            assert error.code == 404
            error.close()


def verify_vector(project):
    sql = "SELECT '[1,2,3]'::vector <-> '[1,2,4]'::vector"
    command = f'psql -XAt -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "{sql}"'
    assert database(project, command, capture_output=True, text=True).stdout.strip() == '1'


def smoke(source, target, archive):
    print('Start the API, ingestion worker and PostgreSQL from clean volumes.', flush=True)
    compose(source, 'up', '--build', '--wait', '--wait-timeout', '120')
    compose(source, 'exec', '-T', 'app', 'python', '-m', 'app.storage', 'load-fixtures')
    verify_vector(source)
    verify_http(source)
    fixture(source, 'seed')

    print('Verify exact money, snapshots and interrupted runs after restart.', flush=True)
    compose(source, 'down')
    compose(source, 'up', '--wait', '--wait-timeout', '120')
    fixture(source, 'verify', 'recovery-before-restart')
    fixture(source, 'interrupt', 'recovery-before-restore')
    compose(source, 'stop', 'app', 'worker')
    backup(source, archive)

    print('Restore into a separate project with fresh database and evidence volumes.', flush=True)
    compose(target, 'build', 'app')
    compose(target, 'up', '--wait', '--wait-timeout', '120', 'db')
    restore(target, archive)
    compose(target, 'up', '--wait', '--wait-timeout', '120')
    verify_vector(target)
    verify_http(target)
    fixture(target, 'verify', 'recovery-before-restart', 'recovery-before-restore')


def main():
    suffix = uuid4().hex[:10]
    source = f'techscout-smoke-{suffix}'
    target = f'{source}-restore'
    os.environ.update(POSTGRES_USER='techscout', POSTGRES_PASSWORD='offline@fixture:pass#1',
                      POSTGRES_DB='techscout', APP_PORT='0', DB_PORT='0',
                      OPENAI_API_KEY='', TAVILY_API_KEY='', FIRECRAWL_API_KEY='')
    with ExitStack() as stack:
        for project in (source, target):
            stack.callback(compose, project, 'down', '--volumes')
        directory = stack.enter_context(tempfile.TemporaryDirectory())
        try:
            smoke(source, target, Path(directory) / 'backup')
        except (Exception, SystemExit):
            for project in (source, target):
                compose(project, 'ps', '--all')
                compose(project, 'logs', '--tail', '25')
            raise
    print('Compose startup, vector query, restart and clean restore passed.')


if __name__ == '__main__':
    main()
