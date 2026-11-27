from types import SimpleNamespace
from unittest import TestCase

from app.agent_inspection import InspectionChoice, read_catalog_listing
from app.page_observation import PageDecision, PageOffer, inspect_capture


class PageAccessTests(TestCase):
    url = 'https://shop.example/headset'
    html = ('<h1>Example headset</h1>' + ' ' * 13500 +
            '<script type="application/ld+json">{"sku":"X-blue","price":"24.50","priceCurrency":"USD",'
            '"itemCondition":"https://schema.org/NewCondition"}</script>'
            '<div hidden>Sold by Example Shop, 5 Main Street, Beirut, Lebanon</div>' + ' ' * 13500)

    def page(self, provider):
        return SimpleNamespace(provider=provider, request_id=None, outcome=SimpleNamespace(body=self.html.encode(),
            final_url=self.url, response_sha256='a' * 64))

    def reader(self, decisions):
        def generate(prompt, data, schema):
            if schema is InspectionChoice:
                return InspectionChoice(action_index=0)
            self.assertTrue(issubclass(schema, PageDecision))
            view = data['page']
            self.assertEqual(view['length'], len(self.html))
            self.assertEqual(view['html'], self.html[view['start']:view['start'] + len(view['html'])])
            decisions.append(view['start'])
            if not view.get('matches'):
                return PageDecision(find=['itemCondition', 'Main Street'])
            snippets = [m['html'] for m in view['matches']]
            self.assertTrue(any('https://schema.org/NewCondition' in s for s in snippets))
            self.assertTrue(any('<div hidden>Sold by Example Shop' in s for s in snippets))
            return PageDecision(fits=True, reason='The selected blue headset is new.',
                offer=PageOffer(title='Example headset', sku='X-blue', seller='Example Shop', brand='Example',
                    color='blue', price=dict(amount='24.50',currency='USD'), price_quote='"price":"24.50"',
                    currency_quote='"priceCurrency":"USD"', condition='NewCondition', condition_quote='NewCondition',
                    merchant_address_quote='Example Shop, 5 Main Street, Beirut, Lebanon'))
        return generate

    def test_buyer_and_catalog_can_inspect_collapsed_text_and_product_data_beyond_the_initial_window(self):
        for provider in ('firecrawl-v2','playwright'):
            page = self.page(provider)
            decisions = []
            generate = self.reader(decisions)
            buyer = inspect_capture(page, 'Find the blue headset', generate)
            role = SimpleNamespace(settings=SimpleNamespace(timeout_seconds=10),
                generate=lambda prompt,data,schema,**kw: SimpleNamespace(status='completed', value=generate(prompt,data,schema)))
            if provider == 'playwright':
                def inspect(job, url, browser):
                    if browser is None:
                        raise TimeoutError()
                    return page
                old = role.generate
                role.generate = lambda prompt,data,schema,**kw: (
                    SimpleNamespace(status='completed',value=InspectionChoice(action_index=1))
                    if schema is InspectionChoice and not data['selected'] else old(prompt,data,schema,**kw))
            else:
                inspect = lambda *args, **kw: page
            catalog = read_catalog_listing(inspect, SimpleNamespace(cancelled=False), self.url,
                                           objective='Refresh this headset', roles=role)
            self.assertEqual(len(decisions), 4)
            self.assertTrue(buyer.match.selected_item_fits)
            self.assertEqual(catalog.selection, buyer.selection)
            self.assertEqual(buyer.selection.condition, 'NewCondition')
            self.assertIn('Beirut', buyer.selection.address_lines[0])

    def test_agent_can_read_any_offset_and_code_cannot_supply_an_offer_on_its_behalf(self):
        views = []
        def generate(prompt, data, schema):
            views.append(data['page'])
            return PageDecision(start=12000) if len(views) == 1 else PageDecision(reason='The seller is unresolved.')
        result = inspect_capture(self.page('playwright'), 'Find an offer', generate)
        self.assertEqual(views[1]['html'], self.html[12000:22000])
        self.assertIsNone(result.selection)
        self.assertEqual(result.reason, 'The seller is unresolved.')

    def test_agent_can_cite_a_fact_outside_the_offer_schema_on_a_contact_page(self):
        self.html = '<details>Weekend pickup &amp; returns\n   until 19:00</details>'
        def generate(prompt, data, schema):
            return PageDecision(reason='Weekend pickup is available.',
                findings=[dict(statement='Weekend pickup until 19:00.', quote='Weekend pickup & returns until 19:00')])
        result = inspect_capture(self.page('firecrawl-v2'), 'When can I collect it?', generate)
        self.assertIsNone(result.selection)
        self.assertEqual(result.findings[0]['quote'], 'Weekend pickup &amp; returns\n   until 19:00')
        self.assertEqual(result.findings[0]['url'], self.url)
