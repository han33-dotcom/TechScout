"""A buyer can resume the exact captured page after checking a different suitable product."""
from test_buyer_flow import FlowCase, inference_config
from test_listing_browser import retailer
from test_public_listing_buyer import URL, listing
from test_shopping_agent import Decisions, development_tools
from fixture_page_agent import fixture_read


class PausedBuyerTests(FlowCase):
    def test_checked_findings_survive_switch_and_saved_read_resumes_without_transfer(self):
        stalled = 'https://new-shop.example/audio/office-headset'
        other = URL
        heading = 'USB microphone headset for calls'
        first = f'<h1>{heading}</h1>' + '<p>USB microphone. Call clarity.</p>' * 1400
        pages = []

        class Buyer(Decisions):
            def __init__(self):
                super().__init__([])
                self.moves = iter([('Inspect selected listing ' + stalled), ('Inspect selected listing ' + other),
                                   'Resume the captured listing', 'Present'])

            def answer(self, role, data):
                if 'page' in data:
                    if data['url'] != stalled:
                        return fixture_read(data).model_dump(mode='json')
                    pages.append(data['page'])
                    if len(pages) == 1:
                        return dict(fits=False, find=['price'], findings=[dict(statement='USB microphone headset for calls.',
                                    quote=heading)], notes='Check whether a listed price was published.')
                    if len(pages) < 4:
                        return dict(fits=False, start=len(pages)*2500, find=['price'], notes='No price found yet.')
                    return dict(fits=False, reason='The checked office headset has no published price.')
                move = next(self.moves)
                if move.startswith('Inspect selected listing ' + other):
                    row = next(row for row in data['checked_pages'] if row['url'] == stalled)
                    assert row['findings'][0]['quote'] == heading
                    assert row['reading_position']['start'] == 7500
                key = next(key for key, label in data['available'].items() if label.startswith(move))
                return dict(action_id=key, answer='The first headset has no published price.' if move == 'Present' else None)

        self.openrouter = Buyer()
        config = inference_config(roles=dict(max_input_tokens=32768, max_output_tokens=2048))
        deps = development_tools(self.deps(config=config), 'headsets')
        with retailer(self.storage, stalled, html=first, links={other: listing('Black', '49.00')}) as (deps.inspect_listing, reads):
            _, run = self.browse(deps, implementation='single_agent', requirements=dict(category='audio'),
                intent=dict(kind='needs', description='I need a headset for calls. Check '+stalled+' and '+other))
        self.assertEqual(reads, [('firecrawl', stalled), ('firecrawl', other)])
        self.assertEqual(len(pages), 4)
        self.assertEqual(pages[-1]['start'], 7500)
        self.assertEqual([a['tool'] for a in self.trace(run)['actions']], ['inspect', 'inspect', 'resume', 'present'])
        self.assertIn('no published price', run.explanation.text)
