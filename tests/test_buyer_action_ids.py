"""Buyer choices identify an operation on a specific page across changing menus."""
from unittest.mock import patch

from app.domestic_buyer import Listing
from app.shopping_agent import ShoppingAgent
from test_buyer_flow import FlowCase
from test_listing_browser import retailer
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
