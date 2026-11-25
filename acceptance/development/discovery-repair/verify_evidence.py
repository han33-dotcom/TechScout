"""Check the browser capture inventory and the pinned cohort using only archived files."""

import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PIN = '37adc26065dd7d6ca35727302950c7fb8f6fc13d'
SERIES = {
    'development': 'D1 D2 D3 D4 D5 D3b D4b D5b D6f D4f',
    'untouched confirmation': 'C2f C3f C4f C5f',
    'post-pin confirmation extension': 'C7f',
    'interrupted confirmation series': 'C1',
    'confirmation replay': 'C1f',
    'reserved overlapping target': 'C6f',
}


def load(path):
    return json.loads(path.read_bytes())


def main():
    requests = {case['id']: case for case in load(ROOT / 'requests.json')}
    expected = {name for attempts in SERIES.values() for name in attempts.split()}
    runs = ROOT / 'runs'
    assert {p.stem for p in runs.glob('*.json')} == expected
    assert {p.stem for p in runs.glob('*.png')} == expected
    run_ids = set()
    visible_urls = set()
    for group, attempts in SERIES.items():
        displayed = verified = 0
        for name in attempts.split():
            record = load(runs / f'{name}.json')
            assert record['attempt'] == name and record['series'] == group
            assert record['run_id'] not in run_ids
            run_ids.add(record['run_id'])
            assert record['input'] == requests[record['case']]
            assert record['accepted_submissions'] == 1
            assert record['catalog_before']['catalog_offers'] == 0
            intent = record['understood']['intent']
            assert intent.get('wording', intent.get('description')) == record['input']['prompt']
            if name.endswith('f'):
                assert record['revision'] == PIN
            assert record['capture'] == f'{name}.png'
            png = (runs / record['capture']).read_bytes()
            assert png.startswith(b'\x89PNG\r\n\x1a\n')
            width, height = struct.unpack('>II', png[16:24])
            assert width == 1280 and height >= 900
            for listing in record['listings']:
                assert listing['listed_price']['state'] == 'known'
                assert listing['receipts']
                visible_urls.add(listing['link'])
            displayed += bool(record['listings'])
            verified += bool(record['verify']['verified'])
        print(f'{group} | {len(attempts.split())} runs | {displayed} displayed | {verified} verified')
    checks = load(ROOT / 'retailer-checks.json')
    assert visible_urls <= {check['url'] for check in checks if check['buyer_visible']}
    print(f'{len(run_ids)} distinct browser runs, {len(requests)} distinct requests')


if __name__ == '__main__':
    main()
