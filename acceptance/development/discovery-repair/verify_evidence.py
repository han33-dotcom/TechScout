"""Check the browser capture inventory and the pinned cohort using only archived files."""

import json
import struct
from decimal import Decimal
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


def verify_cohort(root, product_pin, series, requests):
    expected = {name for attempts in series.values() for name in attempts.split()}
    runs = root / 'runs'
    assert {p.stem for p in runs.glob('*.json')} == expected
    assert {p.stem for p in runs.glob('*.png')} == expected
    run_ids = set()
    visible_offers = []
    for group, attempts in series.items():
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
            if name.endswith(('f', 'z')):
                assert record['revision'] == product_pin
            assert record['capture'] == f'{name}.png'
            png = (runs / record['capture']).read_bytes()
            assert png.startswith(b'\x89PNG\r\n\x1a\n')
            width, height = struct.unpack('>II', png[16:24])
            assert width == 1280 and height >= 900
            for listing in record['listings']:
                assert listing['listed_price']['state'] == 'known'
                assert listing['receipts']
                visible_offers.append(listing)
            displayed += bool(record['listings'])
            verified += bool(record['verify']['verified'])
        print(f'{group} | {len(attempts.split())} runs | {displayed} displayed | {verified} verified')
    checks = load(root / 'retailer-checks.json')
    visible_pages = {check['url']: check for check in checks if check['buyer_visible']}
    for offer in visible_offers:
        page = visible_pages[offer['link']]
        assert page['http'] == 200 and page['final_url'] == offer['link'] and page['response_sha256']
        money = offer['listed_price']['value']
        assert any(product['sku'] == offer['sku']
                   and product['currency'] == money['currency']
                   and Decimal(str(product['price'])) == Decimal(money['amount'])
                   for product in page['products'])
    return run_ids


if __name__ == '__main__':
    final = ROOT / 'final'
    requests = {c['id']: c for folder in (ROOT, final) for c in load(folder / 'requests.json')}
    first_ids = verify_cohort(ROOT, PIN, SERIES, requests)
    final_ids = verify_cohort(final, 'cb2933886cf68b534dae738c8980a5853143ebe1', {
        'final untouched confirmation': 'N1z N2z N3z N4z N5z',
        'development replay of previous confirmation': 'C4z',
    }, requests)
    assert first_ids.isdisjoint(final_ids)
    print(f'{len(first_ids | final_ids)} distinct browser runs, {len(requests)} distinct requests')
