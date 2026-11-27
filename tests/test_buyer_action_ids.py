"""Buyer choices identify an operation on a specific page across changing menus."""
from unittest.mock import patch

from app.domestic_buyer import Listing
from app.shopping_agent import ShoppingAgent
from test_buyer_flow import FlowCase, inference_config
from test_listing_browser import retailer
from test_merchant_location import ADDRESS, CONTACT, LISTING, PRODUCT, LocationChoices
from test_public_listing_buyer import BuyerDecisions, URL, WORDING, listing
from test_shopping_agent import development_tools


class TargetedActionTests(FlowCase):
    def test_saved_choice_survives_menu_growth_and_obsolete_action_is_rejected(self):
        choices = []
        observed_menus = []

        class Buyer(BuyerDecisions):
            def answer(self, role, data):
                if 'available' not in data:
                    return super().answer(role, data)
                menu = data['available']
                observed_menus.append(list(menu))
                if len(observed_menus) == 1:
                    choices.append(next(key for key, label in menu.items() if label.startswith('Inspect selected listing ' + URL)))
                    return dict(action_id='obsolete-action')
                if len(observed_menus) == 2:
                    return dict(action_id=choices[0])
                return dict(action_id='present', answer='The checked black headset is USD 49.00 at Fixture Audio.')

        original = ShoppingAgent.menu
        def grow_menu(agent, final):
            if observed_menus:
                agent.add_lead(Listing('https://another.example/headset'))
                return list(reversed(original(agent, final)))
            return original(agent, final)

        self.openrouter = Buyer([])
        deps = development_tools(self.deps(), 'Q20i')
        with retailer(self.storage, URL, html=listing('Black', '49.00')) as (deps.inspect_listing, reads):
            with patch.object(ShoppingAgent, 'menu', grow_menu):
                _, run = self.browse(deps, implementation='single_agent', requirements=dict(category='audio'),
                    intent=dict(kind='product_link', url=URL, wording=WORDING))
        self.assertNotEqual(observed_menus[0].index(choices[0]), observed_menus[1].index(choices[0]))
        self.assertGreater(len(observed_menus[1]), len(observed_menus[0]))
        self.assertEqual(reads, [('firecrawl', URL)])
        self.assertEqual(str(run.uncertain[0].offer.listing_url), URL)
        self.assertIn('49.00', run.explanation.text)

    def test_buyer_finds_a_deep_contact_link_and_only_saves_supported_seller(self):
        navigation = ''.join(f'<a href="/category/{i}">Category {i}</a>' for i in range(40))
        body = LISTING + navigation + f'<a href="{CONTACT}">Contact the shop</a>'
        for contact, expected_cards in ((ADDRESS, 1), ('<p>Contact form unavailable</p>', 0)):
            with self.subTest(available=bool(expected_cards)):
                class Buyer(LocationChoices):
                    def __init__(self):
                        super().__init__([], True)
                        self.moves = iter(['Inspect selected listing ' + PRODUCT, 'Find a published link',
                                           'Inspect selected listing ' + CONTACT, 'Present'])

                    def answer(self, role, data):
                        if 'page' in data:
                            if data['url'] == CONTACT and not expected_cards:
                                return dict(fits=False, reason='The contact form gives no business address.')
                            return super().answer(role, data)
                        move = next(self.moves)
                        key = next(key for key, label in data['available'].items() if label.startswith(move))
                        if move.startswith('Find'):
                            assert not any(CONTACT in label for label in data['available'].values())
                        return dict(action_id=key, query='contact' if move.startswith('Find') else None,
                                    answer='USD 13 at Fixture Audio.' if expected_cards else 'Seller location unconfirmed.')

                self.openrouter = Buyer()
                config = inference_config(roles={'max_input_tokens': 32768, 'max_output_tokens': 2048})
                deps = development_tools(self.deps(config=config), 'H111')
                with retailer(self.storage, PRODUCT, html=body, links={CONTACT: contact}) as (deps.inspect_listing, reads):
                    _, run = self.browse(deps, implementation='single_agent', requirements=dict(category='audio'),
                        intent=dict(kind='product_link', url=PRODUCT), request_id=f'deep-contact-{expected_cards}')
                self.assertEqual(reads, [('firecrawl', PRODUCT), ('firecrawl', CONTACT)])
                self.assertEqual(len(run.uncertain), expected_cards)
                if expected_cards:
                    self.assertTrue(run.uncertain[0].offer.domestic_seller())
                    self.assertIn(CONTACT, [str(e.final_url) for e in run.uncertain[0].offer.evidence])
