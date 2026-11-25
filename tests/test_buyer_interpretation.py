"""Natural form wording, structured contracts and the search/match boundary."""
import unittest

from app.buyer_api import DomesticShoppingRequest
from app.domestic_search_plan import product_terms, retailer_query
from app.eligibility import check_full_product
from test_domestic_buyer import laptop


def form(text, category, kind='needs', **kwargs):
    intent = {'kind': kind, 'model' if kind == 'exact_model' else 'description': text}
    return DomesticShoppingRequest(request_id='input', intent=intent, requirements={'category': category},
                                   buyer_text=text, **kwargs).interpreted()


class BuyerInterpretationTests(unittest.TestCase):
    def test_exact_paraphrases_keep_suffix_and_specific_finish(self):
        for text in ('Please compare prices for Studio X11 Pro in Space Black with delivery to Sidon.',
                     'What would a new space black Studio X11 Pro cost delivered to Sidon?'):
            request = form(text, 'audio', 'exact_model')
            self.assertEqual(request.intent.wording, text)
            self.assertEqual(retailer_query(request), 'Studio X11 Pro')
            self.assertEqual(request.allowed_colors, ('space black',))
            self.assertEqual(request.fulfillment.destination.city, 'Sidon')

    def test_exact_capacity_is_not_a_minimum(self):
        r = form('Find a Scout Book X with 8GB RAM and 256GB SSD.', 'laptop', 'exact_model')
        self.assertEqual((r.requirements.ram_gb, r.requirements.storage_gb, r.requirements.storage_type),
                         (8, 256, 'SSD'))
        self.assertIsNone(r.requirements.min_ram_gb)
        self.assertEqual(r.intent.model, 'Scout Book X')
        other = form("Could you tell me what's the price of the Scout X Pro Max in blue?", 'phone', 'exact_model')
        self.assertEqual(other.intent.model, 'Scout X Pro Max')
        bounded = form('I would like a tablet with at least 12 GB of RAM.', 'tablet')
        self.assertEqual(bounded.requirements.min_ram_gb, 12)

    def test_queries_and_evidence_use_the_same_minima(self):
        for ram in (8, 64):
            text = f'I need a laptop with at least {ram} GB of RAM and 1 TB storage, for delivery to Tyre.'
            request = form(text, 'laptop')
            self.assertEqual(request.requirements.min_storage_gb, 1024)
            self.assertIn(f'{ram}GB RAM', product_terms(request))
            self.assertEqual(request.intent.description, text)
            checks = check_full_product(request, laptop('test')).checks
            ram_check = next(c for c in checks if c.name == 'ram_gb')
            self.assertEqual(ram_check.outcome, 'fail' if ram > 16 else 'pass')

    def test_negative_unrepresented_clause_and_soft_preference(self):
        request = form('I need a laptop with a privacy shutter. I prefer lightweight and around $880.', 'laptop')
        self.assertEqual(request.preferences, ('portability',))
        self.assertIsNone(request.budget.maximum)
        self.assertEqual(request.budget.target.amount, 880)
        self.assertTrue(any('privacy shutter' in gap for gap in request.unresolved_constraints))
        self.assertNotEqual(check_full_product(request, laptop('test')).outcome, 'pass')
        self.assertIsNone(form('A laptop, preferably 32GB RAM.', 'laptop').requirements.min_ram_gb)
        self.assertIsNone(form('A monitor, not 120Hz.', 'monitor').requirements.min_refresh_hz)

    def test_maximum_and_explicit_destination_conflict(self):
        r = form('A monitor under $350 delivered to Byblos.', 'monitor')
        self.assertEqual(str(r.budget.maximum.amount), '349.99')
        with self.assertRaisesRegex(Exception, 'conflicts with the fulfillment'):
            form('A monitor delivered to Byblos.', 'monitor',
                 fulfillment=dict(mode='pickup', destination=dict(city='Beirut')))

    def test_reading_purpose_keeps_typed_and_unrepresented_requirements(self):
        text = 'I need a Wi-Fi tablet with at least 128 GB of storage for reading textbooks'
        request = form(text, 'tablet')
        self.assertEqual(request.intent.description, text)
        self.assertEqual(request.unresolved_constraints, ())
        self.assertEqual(request.requirements.connectivity, 'wifi')
        self.assertEqual(request.requirements.min_storage_gb, 128)
        restricted = form(text + ' with a waterproof case.', 'tablet')
        self.assertTrue(any('waterproof case' in gap for gap in restricted.unresolved_constraints))

