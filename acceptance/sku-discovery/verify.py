"""Check cohort membership, the browser build, and variant-bound prices and totals offline."""
import json
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load(name):
    return json.loads((ROOT / name).read_bytes())


def verify():
    build = load('build.json')
    cases = {c['id']: c for c in load('confirmation.json')}
    references = {c['id']: c for c in load('references.json')['cases']}
    assert references.keys() == cases.keys()
    scores = load('outcomes.json')
    assert {s['case'] for s in scores} == cases.keys() and len(scores) == len(cases)
    rows = {path.stem: load(path) for path in (ROOT / 'runs').glob('*.json')}
    assert set(rows) == set(cases) | set(build['development_replays'])
    assert len({r['run_id'] for r in rows.values()}) == len(rows)
    for key, row in rows.items():
        assert row['attempt'] == key and row['accepted_submissions'] == 1
        assert row['revision'] == build['revision']
        assert row['catalog_before']['catalog_offers'] == 0
        posted = row['submitted_form']
        assert posted['buyer_text'] == row['input']['prompt']
        supplied = {k:v for k,v in posted['requirements'].items() if v is not None and v != []}
        assert supplied == {'category': row['input']['category']}
        assert posted['fulfillment'] == {'mode':'any', 'branch_id':None, 'destination':{'country':'LB','city':None}}
        expected_intent = {'kind': row['input']['entry'],
                                    ('description' if row['input']['entry'] == 'needs' else 'model'): posted['buyer_text']}
        if row['input']['entry'] == 'needs':
            expected_intent['required_software'] = []
        assert posted['intent'] == expected_intent
        assert row['source_registry_sha256'] == build['source_registry_sha256']
        assert (ROOT / 'runs' / row['capture']).read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
        if key in cases:
            assert row['series'] == 'untouched confirmation'
            assert row['input'] == cases[key]
            intent = row['understood']['intent']
            assert intent.get('wording', intent.get('description')) == cases[key]['prompt']
        else:
            assert row['series'] == 'development replay'
        visible = {link['url'] for link in row['visible_links']}
        for offer in row['listings']:
            assert offer['link'] in visible
            receipts = {e['evidence_id']: e for e in offer['receipts']}
            for field, path in [('listed_price', 'price'), ('stock', 'stock')]:
                fact = offer[field]
                assert fact['state'] == 'known' and fact['evidence_ids']
                for evidence_id in fact['evidence_ids']:
                    receipt = receipts[evidence_id]
                    assert (receipt['run']['run_id'], receipt['variant_id'], receipt['field_path']) == (
                        row['run_id'], offer['sku'], path)
            if offer['state'] == 'verified':
                amount = Decimal(offer['listed_price']['value']['amount'])
                for charge in offer['charges']:
                    assert charge['state'] in {'included', 'additional', 'not_applicable'}
                    if charge['state'] == 'additional':
                        amount += Decimal(charge['amount']['amount'])
                assert amount == Decimal(offer['cost']['total']['amount'])
    for score in scores:
        listings = rows[score['case']]['listings']
        if score['correct_page']:
            links = {url for s in rows[score['case']]['retailer_searches'] for url in s['links']}
            assert any(p['url'] in links and score['case'] in p['cases'] and p['status'] == 200
                       and p['stock'] == 'https://schema.org/InStock' for p in load('page-rechecks.json'))
        assert score['current_price'] == bool(listings)
        assert not score['confirmed'] or score['correct_page']
        assert score['delivered_total'] == any(o['state'] == 'verified' for o in listings)
    for stage in ('correct_page', 'confirmed', 'current_price', 'delivered_total'):
        print(stage, sum(s[stage] for s in scores), '/', len(cases))
    print(len(rows) - len(cases), 'separate development replays')


if __name__ == '__main__':
    verify()
