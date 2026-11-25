"""Verify first submissions, product pins, buyer-visible links and field-level price/total evidence."""
from decimal import Decimal
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = '5af1900af3161574df245bc380198592a8b0cce2'
PIN = '7670e463614755ff891f98cee409d2f451bd9e39'


def read(path):
    return json.loads(path.read_bytes())


def verify():
    cases = {case['id']: case for case in read(ROOT / 'confirmation.json')}
    build = read(ROOT / 'build-check.json')
    assert build['product_revision'] == PIN
    assert build['confirmation_trace_version'] == PIN + '+uncommitted'
    assert set(build['tracked_differences']) == {'frontend/src/Comparison.test.tsx', 'tests/test_technodel_audio.py'}
    assert not build['untracked_product_paths']
    expected = {case + 'f' for case in cases} | {'A1', 'A2', 'A3', 'A1r', 'A2r', 'A3r', 'R3r', 'R4r', 'R5r'}
    paths = sorted((ROOT / 'runs').glob('*.json'))
    assert {p.stem for p in paths} == expected
    ids = set()
    for path in paths:
        row = read(path)
        assert row['attempt'] == path.stem and row['run_id'] not in ids
        ids.add(row['run_id'])
        revision = build['confirmation_trace_version'] if path.stem.endswith('f') else PIN
        assert row['revision'] == (BASE if row['series'] == 'development baseline' else revision)
        assert row['accepted_submissions'] == 1 and row['catalog_before']['catalog_offers'] == 0
        assert path.with_suffix('.png').read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
        if path.stem.endswith('f'):
            case = cases[row['case']]
            assert row['input'] == case
            intent = row['understood']['intent']
            assert intent.get('wording', intent.get('description')) == case['prompt']
            assert row['series'] == ('overlap audit' if row['case'] == 'F3' else 'untouched confirmation')
        visible = {link['url'] for link in row['visible_links']}
        for offer in row['listings']:
            assert offer['link'] in visible
            receipts = {e['evidence_id']: e for e in offer['receipts']}
            for name, field in [('listed_price', 'price'), ('stock', 'stock')]:
                fact = offer[name]
                assert fact['state'] == 'known' and fact['evidence_ids']
                for key in fact['evidence_ids']:
                    evidence = receipts[key]
                    assert (evidence['field_path'], evidence['run']['run_id'], evidence['variant_id']) == (
                        field, row['run_id'], offer['sku'])
            if offer['state'] == 'verified':
                assert offer['cost']['status'] == 'verified'
                amount = Decimal(offer['listed_price']['value']['amount'])
                for charge in offer['charges']:
                    assert charge['state'] in {'included', 'additional', 'not_applicable'}
                    if charge['state'] == 'additional':
                        amount += Decimal(charge['amount']['amount'])
                assert amount == Decimal(offer['cost']['total']['amount'])
    print(f'{len(ids)} distinct browser submissions, including six untouched and one overlapping request.')


if __name__ == '__main__':
    verify()
