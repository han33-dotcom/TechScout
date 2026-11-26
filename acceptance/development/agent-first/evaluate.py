"""Run isolated original submissions against an archived single-agent build."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import socket
import subprocess
import sys
import time

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PAIR = HERE.parent / 'paired-shopping'
sys.path.insert(0, str(PAIR))
import pair

FREEZE = ROOT / 'acceptance/architecture-v2'
LABELS = dict(phone='Phones', audio='Headphones & earbuds', laptop='Laptops',
              tablet='Tablets', monitor='Monitors')


def read(path):
    return json.loads(path.read_bytes())


def environment(work, database, evidence):
    return dict(os.environ, DATABASE_URL='sqlite:///' + str(database),
                EVIDENCE_DIR=str(evidence), PYTHONPATH=str(work / 'agent'))


def hashes(root):
    files = [p for folder in ('app', 'config', 'frontend/dist', 'docs', 'migrations')
             for p in (root / folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    return {p.relative_to(root).as_posix(): pair.hash_file(p) for p in files}


def prepare(args):
    work = args.work
    work.mkdir(parents=True)
    revision = subprocess.check_output(['git', 'rev-parse', args.revision + '^{commit}'],
                                       cwd=ROOT, text=True).strip()
    pair.PINS['agent'] = revision
    product = pair.product(work, 'agent')
    adapter = work / 'adapter'
    adapter.mkdir()
    for name, source in pair.adapter_sources().items():
        shutil.copyfile(source, adapter / name)
    manifest = read(FREEZE / 'manifest-v2.json')
    runpy.run_path(str(FREEZE / 'verify.py'))['verify_catalog'](args.seed, manifest['catalog'])
    seed = work / 'seed.sqlite'
    shutil.copyfile(args.seed, seed)
    env = environment(work, seed, work / 'setup-evidence')
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=product, env=env, check=True)
    probe = work / 'preflight.sqlite'
    shutil.copyfile(seed, probe)
    subprocess.run([sys.executable, str(adapter / 'profile.py'), str(args.env_file),
                    str(work / 'profile.json')], cwd=product,
                   env=environment(work, probe, work / 'probe-evidence'), check=True)
    pair.save(work / 'lock.json', dict(revision=revision, files=hashes(product),
              directory=manifest['catalog']['directory_sha256'], original_seed=pair.hash_file(args.seed),
              seed=pair.hash_file(seed), budgets=manifest['budget'], profile=pair.hash_file(work / 'profile.json'),
              adapter={p.name: pair.hash_file(p) for p in adapter.glob('*.py')},
              driver=pair.hash_file(Path(__file__))))


def submit(browser, url, row, out):
    with browser.new_context() as context:
        page = context.new_page()
        page.goto(url)
        page.get_by_role('button', name=LABELS[row['category']], exact=True).click()
        page.get_by_label('Shopping assistant').select_option('single_agent')
        page.get_by_role('combobox', name=re.compile('^Search for')).select_option(
            'needs' if row['entry'] == 'needs' else 'exact_model')
        page.locator('#request').fill(row['prompt'])
        page.get_by_label('City in Lebanon').fill(row['destination'])
        page.get_by_role('combobox', name=re.compile('^Fulfillment')).select_option(row['mode'])
        page.evaluate("""() => {
          const events = window.displayEvents = [];
          document.querySelector('form.request-form').addEventListener('submit', () => {
            window.startTime = performance.now();
          }, {capture:true, once:true});
          new MutationObserver(() => {
            if (window.startTime === undefined) return;
            const text = [...document.querySelectorAll('.provisional article, article.recommendation, .uncertain article, .progress p, .progress form, [role=alert]')]
              .filter(e => e.getClientRects().length).map(e => e.innerText).join('\\n');
            const status = document.querySelector('.progress [role=status]')?.innerText;
            if (events.at(-1)?.text !== text || events.at(-1)?.status !== status)
              events.push({ms:Math.round(performance.now()-window.startTime), text, status});
          }).observe(document.body,{subtree:true,attributes:true,characterData:true,childList:true});
        }""")
        start, stamp = pair.timed_click(page.get_by_role('button', name='Find my options'))
        terminal = page.get_by_role('status').filter(has_text=pair.ENDINGS)
        while time.monotonic() - start < 300 and not terminal.count() and not page.get_by_role('alert').count():
            page.wait_for_timeout(150)
        elapsed = round(time.monotonic() - start, 3)
        links = page.locator('.comparison a:visible').evaluate_all(
            "items => items.map(a => ({text:a.innerText,url:a.href}))")
        pair.save(out / 'buyer.json', dict(input=row, submitted=stamp, elapsed_s=elapsed,
                  terminal=terminal.inner_text() if terminal.count() else 'error or browser cap',
                  browser=browser.version, links=links, timeline=page.evaluate('window.displayEvents'),
                  buyer=page.locator('body').inner_text()))
        page.screenshot(path=str(out / 'buyer.png'), full_page=True)


def run(args):
    work = args.work
    lock = read(work / 'lock.json')
    adapter = work / 'adapter'
    if (hashes(work / 'agent') != lock['files'] or pair.hash_file(work / 'seed.sqlite') != lock['seed']
            or pair.hash_file(work / 'profile.json') != lock['profile']
            or {p.name: pair.hash_file(p) for p in adapter.glob('*.py')} != lock['adapter']
            or pair.hash_file(Path(__file__)) != lock['driver']):
        raise ValueError('Evaluated files differ from preparation')
    primary = args.operation == 'primary'
    if primary and not read(work / 'profile.json')['provider_preflight_passed']:
        raise ValueError('Required agent provider preflight failed')
    rows = read(FREEZE / 'primary-inputs.json' if primary else HERE / 'requests.json')
    if primary:
        manifest = read(FREEZE / 'manifest-v2.json')
        assert [r['id'] for r in rows] == manifest['cohorts']['primary']['ids']
    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        for row in rows:
            out = work / args.operation / row['id']
            if (out / 'complete.json').exists():
                continue
            out.mkdir(parents=True)
            db = out / 'catalog.sqlite'
            shutil.copyfile(work / 'seed.sqlite', db)
            env = environment(work, db, out / 'evidence')
            env['TECHSCOUT_CODE_VERSION'] = lock['revision']
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            cmd = [sys.executable, str(adapter / 'serve.py'), '--arm', 'agent', '--mode', 'live',
                   '--work', str(out), '--bank', str(out / 'bank'), '--port', str(port),
                   '--env-file', str(args.env_file), '--profile', str(work / 'profile.json'),
                   '--destination-ref', row['id'], '--input', row['prompt'], '--category', row['category'],
                   '--city', row['destination'], '--fulfillment', row['mode']]
            with (out / 'server.log').open('w') as log:
                process = subprocess.Popen(cmd, cwd=work / 'agent', env=env, stdout=log, stderr=log)
                try:
                    pair.wait_server(process, f'http://127.0.0.1:{port}')
                    submit(browser, f'http://127.0.0.1:{port}', row, out)
                    for _ in range(100):
                        if list((out / 'traces').glob('*.json')):
                            break
                        time.sleep(.1)
                finally:
                    process.terminate()
                    process.wait(timeout=15)
            pair.save(out / 'complete.json', dict(lock=pair.hash_file(work / 'lock.json'),
                      bank=pair.bank_hash(out / 'bank'), finished=datetime.now(timezone.utc).isoformat()))
            print(row['id'], read(out / 'buyer.json')['terminal'], flush=True)
        browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('prepare', 'development', 'primary'))
    parser.add_argument('--work', type=lambda s: Path(s).resolve(), required=True)
    parser.add_argument('--revision', default='HEAD')
    parser.add_argument('--seed', type=lambda s: Path(s).resolve())
    parser.add_argument('--env-file', type=lambda s: Path(s).resolve(), default=ROOT / '.env')
    args = parser.parse_args()
    if args.operation == 'prepare':
        if not args.seed:
            parser.error('Preparation requires a seed from the pinned control')
        prepare(args)
    else:
        run(args)
