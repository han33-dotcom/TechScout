"""Build frozen products and submit fresh development requests through their buyer forms."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from urllib.request import urlopen
import zipfile

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
FREEZE = ROOT / 'acceptance/architecture-v2'
PINS = {'control': '34918b296c1615cba2741eb6962d5b8ba185ddad',
        'agent': 'c9c71033d7daf5508683e216f165d77c3eaab026'}
INPUTS = {'setup': ('Honor X9d', 'Tripoli'),
          'search': ('Samsung Galaxy A07s', 'Batroun'),
          'selection': ('https://classcellulars.com/product/samsung-galaxy-a07s/', 'Batroun')}
ENDINGS = re.compile('^(Your search is complete|Some checks could not finish|No verified recommendation yet'
                     '|Search cancelled|Search could not finish|One detail to clarify)$')


def hash_file(path):
    return sha256(path.read_bytes()).hexdigest()


def bank_hash(path):
    rows = [(p.name, hash_file(p)) for p in sorted(path.glob('*.json'))]
    return sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, indent=2, default=str), encoding='utf-8')


def product(work, arm):
    target = work / arm
    target.mkdir()
    files = ['app', 'config', 'frontend', 'migrations', 'alembic.ini', 'docs/retailer-audit-2026-11-16.json']
    archive = subprocess.check_output(['git', 'archive', '--format=zip', PINS[arm], *files], cwd=ROOT)
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        package.extractall(target)
    npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
    for command in (['ci', '--no-audit', '--no-fund'], ['run', 'build']):
        subprocess.run([npm, *command], cwd=target / 'frontend', check=True)
    return target


def prepare(work, env_file):
    work.mkdir(parents=True)
    adapter = work / 'adapter'
    adapter.mkdir()
    for script in HERE.glob('*.py'):
        shutil.copyfile(script, adapter / script.name)
    roots = {arm: product(work, arm) for arm in PINS}
    seed = work / 'seed.sqlite'
    env = dict(os.environ, DATABASE_URL='sqlite:///' + str(seed), PYTHONPATH=str(roots['control']),
               EVIDENCE_DIR=str(work / 'seed-evidence'))
    for command in (['-m', 'alembic', 'upgrade', 'head'], ['-m', 'app.directory', 'import-seeds']):
        subprocess.run([sys.executable, *command], cwd=roots['control'], env=env, check=True)
    manifest = json.loads((FREEZE / 'manifest-v2.json').read_bytes())
    runpy.run_path(str(FREEZE / 'verify.py'))['verify_catalog'](seed, manifest['catalog'])
    probes = {}
    for arm, root in roots.items():
        database = work / (arm + '-preflight.sqlite')
        shutil.copyfile(seed, database)
        output = work / (arm + '-preflight.json')
        probe_env = dict(env, DATABASE_URL='sqlite:///' + str(database), PYTHONPATH=str(root),
                         EVIDENCE_DIR=str(work / (arm + '-preflight-evidence')))
        subprocess.run([sys.executable, str(adapter / 'profile.py'), str(env_file), str(output)],
                       cwd=root, env=probe_env, check=True)
        probes[arm] = json.loads(output.read_bytes())
    a, b = probes.values()
    if a['rates'] != b['rates'] or a['managed_scrape'] != b['managed_scrape']:
        raise ValueError('The two dependency graphs configured different provider access')
    save(work / 'profile.json', dict(channels=[k for k in a['channels'] if k in b['channels']],
         rates=a['rates'], managed_scrape=a['managed_scrape'], probes=probes,
         provider_preflight_passed=all(p['provider_preflight_passed'] for p in probes.values())))
    files = {}
    for arm, root in roots.items():
        files[arm] = {f.relative_to(root).as_posix(): hash_file(f) for f in root.rglob('*')
                      if f.is_file() and ('node_modules' not in f.parts and '__pycache__' not in f.parts)
                      and f.suffix != '.pyc'}
    lock = dict(pins=PINS, files=files, seed=hash_file(seed), profile=hash_file(work / 'profile.json'),
                directory=manifest['catalog']['directory_sha256'], budgets=manifest['budget'],
                adapter={p.name: hash_file(p) for p in adapter.glob('*.py')})
    save(work / 'lock.json', lock)


def wait_server(process, url):
    for _ in range(200):
        if process.poll() is not None:
            raise RuntimeError('Worker failed during startup; see server.log')
        try:
            with urlopen(url, timeout=1):
                return
        except OSError:
            time.sleep(.15)
    raise RuntimeError('Worker did not start')


def timed_click(button):
    # Concurrent runner invocations in this checkout share the submission clock.
    clock = ROOT / 'var/paired-shopping-clock.sqlite'
    clock.parent.mkdir(exist_ok=True)
    with sqlite3.connect(clock, timeout=180) as db:
        db.execute('CREATE TABLE IF NOT EXISTS submission (id INTEGER PRIMARY KEY, at REAL)')
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT at FROM submission WHERE id=1').fetchone()
        if row:
            time.sleep(max(0, 61 - (time.time() - row[0])))
        started = time.monotonic()
        submitted = datetime.now(timezone.utc).isoformat()
        button.click()
        db.execute('INSERT OR REPLACE INTO submission VALUES (1, ?)', (time.time(),))
    return started, submitted


def submit(engine, url, arm, case, out):
    text, city = INPUTS[case]
    context = engine.new_context()
    page = context.new_page()
    try:
        page.goto(url)
        page.get_by_role('button', name='Phones', exact=True).click()
        page.get_by_role('combobox', name=re.compile('^Search for')).select_option('exact_model')
        if arm == 'agent':
            page.get_by_label('Shopping assistant').select_option('single_agent')
        page.locator('#request').fill(text)
        page.get_by_label('City in Lebanon').fill(city)
        page.get_by_role('combobox', name=re.compile('^Fulfillment')).select_option('delivery')
        started, submitted = timed_click(page.get_by_role('button', name='Find my options'))
        first = None
        evidence_display = None
        while time.monotonic() - started < 300:
            if first is None and page.locator('.provisional article').count():
                first = round(time.monotonic() - started, 3)
            if evidence_display is None and page.locator('.offer-details, .uncertain article').count():
                evidence_display = round(time.monotonic() - started, 3)
            terminal = page.get_by_role('status').filter(has_text=ENDINGS)
            if terminal.count():
                break
            page.wait_for_timeout(200)
        elapsed = round(time.monotonic() - started, 3)
        buyer = page.locator('body').inner_text()
        page.screenshot(path=str(out / 'buyer.png'), full_page=True)
        save(out / 'buyer.json', dict(arm=arm, case=case, text=text, city=city, submitted=submitted,
            browser=engine.version, elapsed_s=elapsed, first_provisional_s=first,
            first_offer_display_s=evidence_display,
            terminal=terminal.inner_text() if terminal.count() else 'browser timeout', buyer=buyer))
    finally:
        context.close()


def run(args):
    work = args.work
    lock = json.loads((work / 'lock.json').read_bytes())
    adapter = work / 'adapter'
    if (lock['pins'] != PINS or hash_file(work / 'seed.sqlite') != lock['seed']
            or hash_file(work / 'profile.json') != lock['profile']
            or hash_file(Path(__file__)) != lock['adapter']['pair.py']
            or lock['adapter'] != {p.name: hash_file(p) for p in adapter.glob('*.py')}):
        raise ValueError('Seed, profile or adapter changed since preparation')
    output = work / args.label
    output.mkdir()
    source_bank = bank_hash(args.bank) if args.mode == 'replay' else None
    with sync_playwright() as driver:
        engine = driver.chromium.launch()
        for arm in PINS:
            root = work / arm
            for name, expected in lock['files'][arm].items():
                if hash_file(root / name) != expected:
                    raise ValueError('Pinned product/build changed: ' + name)
            out = output / arm
            out.mkdir()
            db = out / 'catalog.sqlite'
            shutil.copyfile(work / 'seed.sqlite', db)
            env = dict(os.environ, DATABASE_URL='sqlite:///' + str(db), EVIDENCE_DIR=str(out / 'evidence'),
                       PYTHONPATH=str(root), TECHSCOUT_CODE_VERSION=PINS[arm])
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            bank = args.bank if args.mode == 'replay' else out / 'bank'
            cmd = [sys.executable, str(adapter / 'serve.py'), '--arm', arm, '--mode', args.mode,
                   '--work', str(out), '--bank', str(bank), '--port', str(port), '--env-file', str(args.env_file),
                   '--destination-ref', args.case + '-city', '--profile', str(work / 'profile.json'),
                   '--input', INPUTS[args.case][0], '--city', INPUTS[args.case][1]]
            with (out / 'server.log').open('w') as log:
                proc = subprocess.Popen(cmd, cwd=root, env=env, stdout=log, stderr=log)
                try:
                    url = 'http://127.0.0.1:' + str(port)
                    wait_server(proc, url)
                    submit(engine, url, arm, args.case, out)
                    # The UI can publish a terminal snapshot just before the controller flushes its trace.
                    for _ in range(100):
                        if list((out / 'traces').glob('*.json')):
                            break
                        time.sleep(.1)
                finally:
                    proc.terminate()
                    proc.wait(timeout=15)
            fingerprint = bank_hash(bank)
            if source_bank is not None and fingerprint != source_bank:
                raise ValueError('Replay bank changed during the comparison')
            save(out / 'run-lock.json', dict(arm=arm, product=PINS[arm], adapter=lock['adapter'],
                 seed=lock['seed'], catalog=lock['directory'], budgets=lock['budgets'], profile=lock['profile'],
                 mode=args.mode, bank=fingerprint, served_assets={p: h for p, h in lock['files'][arm].items()
                 if p.startswith('frontend/dist/')}, model_config=lock['files'][arm]['config/evaluation.json'],
                 source_policy=lock['files'][arm]['config/sources.json'], browser=engine.version))
        engine.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('prepare', 'run'))
    parser.add_argument('--work', required=True, type=lambda x: Path(x).resolve())
    parser.add_argument('--env-file', type=lambda x: Path(x).resolve(), default=ROOT / '.env')
    parser.add_argument('--case', choices=INPUTS, default='setup')
    parser.add_argument('--mode', choices=('live', 'replay'), default='live')
    parser.add_argument('--bank', type=lambda x: Path(x).resolve())
    parser.add_argument('--label', default='development')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', args.label):
        parser.error('Use a new simple output label')
    if args.mode == 'replay' and not args.bank:
        parser.error('Replay needs a common captured --bank')
    if args.operation == 'prepare':
        prepare(args.work, args.env_file)
    else:
        run(args)


if __name__ == '__main__':
    main()
