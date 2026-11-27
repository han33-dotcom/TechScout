"""Separate development inputs. No scored requests or evaluation evidence are loaded."""
from contextlib import contextmanager
import json
from unittest.mock import Mock, patch

import httpx2
from app import contracts_v2 as v2
from app.domestic_buyer_sources import ReadResult
from app.domestic_discovery import Attribution, ChannelAttempt, SearchLead, SearchReport
from test_buyer_flow import FakeOpenRouter, FlowCase
from test_domestic_buyer import Sources, laptop, lead


class DevelopmentSearch:
    connectors = {'serpapi': object(), 'brave': object()}

    def __init__(self, offer):
        self.offer = offer

    def search(self, query, job, *, alternatives):
        url = str(self.offer.listing_url)
        attempts = (ChannelAttempt(query.channel, 'ok'),)
        if self.offer.variant.product.model == 'Aster Finch Q14' and query.channel == 'serpapi':
            return SearchReport(job.run_id, attempts, ())
        return SearchReport(job.run_id, attempts, (lead(url, self.offer),))


def development_tools(deps, model):
    offer = laptop('workbench', model=model, price='712.35')
    deps.domestic_sources = Sources([], [offer])
    deps.research.workers.domestic_tools = DevelopmentSearch(offer)
    return deps


class Decisions(FakeOpenRouter):
    def __init__(self, sequence):
        super().__init__()
        self.sequence = iter(sequence)

    def role(self, instructions):
        return 'shopping_agent'

    def answer(self, role, data):
        prefix = next(self.sequence)
        key = next(key for key, label in data['available'].items() if label.startswith(prefix))
        intent = data['request']['intent']
        return dict(action_id=key, offer_ids=[v['offer_id'] for v in data['verified']],
                    answer='Here is the checked listing evidence.' if prefix.startswith('Present') else None,
                    query=intent.get('model', 'headphones') if prefix.startswith(('Search', 'Look up')) else None,
                    question='Which configuration do you need?' if prefix == 'Ask' else None,
                    material_reason='Memory changes the available models.' if prefix == 'Ask' else None)


class ShoppingAgentTests(FlowCase):
    def test_experimental_buyer_gets_a_closing_turn_for_a_missing_answer(self):
        class MissingAnswer(Decisions):
            def answer(self, role, data):
                decision = super().answer(role, data)
                decision['answer'] = 'I could not verify a matching listing.' if len(data['available']) == 1 else None
                return decision

        self.openrouter = MissingAnswer(['Present', 'Present'])
        deps = development_tools(self.deps(), 'TestBrand H14')
        deps.inspect_listing = Mock()
        _, run = self.browse(deps, implementation='single_agent', requirements=dict(category='audio'),
                             intent=dict(kind='exact_model', model='TestBrand H14'))
        self.assertEqual(run.explanation.source, 'model')
        self.assertEqual(run.explanation.text, 'I could not verify a matching listing.')
        self.assertEqual(self.trace(run)['actions'][-1]['status'], 'missing_answer')

    def test_missing_buyer_answer_reports_the_interruption(self):
        class NeverAnswers(Decisions):
            def answer(self, role, data):
                return {**super().answer(role, data), 'answer': None}

        self.openrouter = NeverAnswers(['Present', 'Present'])
        deps = development_tools(self.deps(), 'TestBrand H14')
        deps.inspect_listing = Mock()
        _, run = self.browse(deps, implementation='single_agent', requirements=dict(category='audio'),
                             intent=dict(kind='exact_model', model='TestBrand H14'))
        self.assertEqual(run.explanation.source, 'fallback')
        self.assertEqual(run.explanation.text, 'I could not verify a retailer offer for this request before the search ended.')

    def test_connection_failure_retries_the_decision_without_repeating_a_tool(self):
        self.openrouter = Decisions(['Look up', 'Present'])
        attempts = iter([False, True, False])
        def fail_second_call(_role):
            if next(attempts):
                raise httpx2.ConnectError('connection dropped before response')
        self.openrouter.gate = fail_second_call
        deps = development_tools(self.deps(), 'TestBrand H14')
        deps.domestic_sources.lookup = Mock(return_value=())
        _, run = self.browse(deps, implementation='single_agent', requirements=dict(category='audio'),
                             intent=dict(kind='exact_model', model='TestBrand H14'))
        deps.domestic_sources.lookup.assert_called_once()
        trace = self.trace(run)
        self.assertEqual([c['status'] for c in trace['model_calls']], ['completed', 'transport_error', 'completed'])
        self.assertEqual([a['tool'] for a in trace['actions']], ['catalog', 'present'])

    def test_original_request_drives_catalog_and_reformulated_searches(self):
        wording = ('Which of two Lebanese shops has the lower price for a new wired TestBrand H14? '
                   'Show both listings before delivery.')
        choices = [('Look up', 'TestBrand H14'), ('Search serpapi', 'buyer@example.org'),
                   ('Search serpapi', 'TestBrand H14 Beirut'), ('Search serpapi', 'سماعة H14 لبنان'),
                   ('Search reviewed retailer', 'H14'), ('Present', None)]

        class AgentQueries(Decisions):
            def answer(self, role, data):
                prefix, query = next(self.sequence)
                key = next(key for key, label in data['available'].items() if label.startswith(prefix))
                return dict(action_id=key, query=query, language='ar' if query and 'لبنان' in query else 'en')

        self.openrouter = AgentQueries(choices)
        deps = development_tools(self.deps(), 'TestBrand H14')
        catalog = deps.domestic_sources.lookup = Mock(return_value=())
        retailer = deps.domestic_sources.search_listings = Mock(return_value=())
        search = deps.research.workers.domestic_tools
        search.search = Mock(side_effect=lambda objective, job, **_: SearchReport(job.run_id, (), ()))
        with patch('app.buyer_api.interpret', side_effect=AssertionError('legacy interpretation')):
            response = self.client.post('/api/runs', headers=self.headers, json=dict(
                request_id='agent-authored-search', implementation='single_agent', buyer_text=wording,
                intent=dict(kind='exact_model', model=wording), requirements=dict(category='audio'),
                budget=dict(maximum=dict(amount='50', currency='USD'))))
        self.assertEqual(response.status_code, 202, response.text)
        snapshot = v2.RunSnapshot.model_validate(response.json())
        self.work(deps)
        _, finished = self.read(snapshot)
        self.assertEqual(finished.request.intent.wording, wording)
        self.assertEqual(str(finished.request.budget.maximum.amount), '50')
        self.assertEqual(catalog.call_args.kwargs['query'], 'TestBrand H14')
        self.assertEqual(retailer.call_args.kwargs['query'], 'H14')
        self.assertEqual([(call.args[0].query, call.args[0].language) for call in search.search.call_args_list],
                         [('TestBrand H14 Beirut', 'en'), ('سماعة H14 لبنان', 'ar')])
        decisions = [json.loads(call['body']['messages'][1]['content']) for call in self.openrouter.requests]
        self.assertTrue(all(d['request']['intent']['wording'] == wording for d in decisions))
        self.assertEqual(decisions[2]['observations'][-1]['status'], 'invalid_query')
        self.assertNotIn('buyer@example.org', json.dumps(self.trace(finished)))

    def agent(self, model, ram, sequence):
        self.openrouter = Decisions(sequence)
        deps = development_tools(self.deps(), model)
        return self.browse(deps, implementation='single_agent',
            requirements=dict(category='laptop', min_ram_gb=ram),
            intent=dict(kind='exact_model', model=model, wording=f'Find {model} for delivery'),
            refinement_note='Compare all mandatory fees.')[1]

    def test_switch_channel_after_search_misses_listing(self):
        run = self.agent('Aster Finch Q14', 16, ['Search serpapi', 'Search brave', 'Inspect', 'Present'])
        self.assertEqual(run.status, 'complete', self.trace(run))
        self.assertEqual(str(run.recommendations[0].primary.cost.total.amount), '722.35')
        trace = self.trace(run)
        self.assertEqual([a.get('count') for a in trace['actions']], [0, 1, 1, None])
        self.assertEqual(trace['limits']['ledger_buyer_caps']['seconds'], 300)
        self.assertEqual(trace['limits']['nonfinal_by_s'], 265)
        self.assertGreater(trace['limits']['final_by_s'], 290)
        self.assertLessEqual(trace['limits']['final_by_s'], 298)

    def test_wrong_selected_memory_is_rejected(self):
        run = self.agent('Rowan Slate W8', 32, ['Search serpapi', 'Inspect', 'Present'])
        self.assertEqual(run.status, 'abstained', self.trace(run))
        self.assertFalse(run.recommendations or run.uncertain)
        self.assertIn('RAM requirement (listing has 16 GB)', run.stop_reason)
        self.assertIn('Rowan Slate W8', run.stop_reason)

    def test_unreviewed_title_informs_scoped_search_without_becoming_an_inspection(self):
        model = 'Rowan Slate W8 Plus'
        self.openrouter = Decisions(['Search serpapi', 'Search workbench.example', 'Present'])
        deps = development_tools(self.deps(), model)
        search = deps.research.workers.domestic_tools
        queries = []
        reviewed = lead(str(search.offer.listing_url), search.offer)
        unknown = SearchLead('https://other.example/product/w8-plus', 'host:other.example', None,
                             (Attribution('serpapi', None, 'W8 Plus listed', '', None, None),))

        def results(query, job, *, alternatives):
            queries.append(query.query)
            return SearchReport(job.run_id, (ChannelAttempt(query.channel, 'ok'),), (reviewed, unknown))

        search.search = results
        self.browse(deps, implementation='single_agent', requirements=dict(category='laptop'),
                    intent=dict(kind='exact_model', model=model))
        self.assertEqual(queries[1], 'site:workbench.example Rowan Slate W8 Plus')
        decision = json.loads(self.openrouter.requests[1]['body']['messages'][1]['content'])
        self.assertEqual(decision['observations'][0]['unreviewed_search_titles'],
                         [dict(host='other.example', title='W8 Plus listed')])
        self.assertFalse(any('other.example' in action for action in decision['available'].values()))

    def test_question_and_feedback_reach_resumed_agent(self):
        run = self.agent('Rowan Slate W8', 32, ['Ask'])
        self.assertEqual(run.status, 'clarification', self.trace(run))
        response = self.client.post(f'/api/runs/{run.run.run_id}/clarify', headers=self.headers,
            json=dict(run=run.run.model_dump(), clarification_id=run.clarification.clarification_id,
                      answer='I need 32 GB. Check https://workbench.example/workbench'))
        self.assertEqual(response.status_code, 202)
        self.openrouter = Decisions(['Inspect', 'Present'])
        self.work(development_tools(self.deps(), 'Rowan Slate W8'))
        data = json.loads(self.openrouter.requests[-1]['body']['messages'][1]['content'])
        self.assertEqual(data['request']['intent']['wording'], 'Find Rowan Slate W8 for delivery')
        self.assertEqual(data['request']['refinement_note'], 'Compare all mandatory fees.')
        self.assertEqual(data['feedback'][0]['question'], run.clarification.question)
        self.assertIn('32 GB', data['feedback'][0]['answer'])

    def test_stateful_adapter_observation_supersedes_previous_verification(self):
        events = []
        self.openrouter = Decisions(['Search serpapi', 'Inspect', 'Select variant', 'Present'])
        deps = development_tools(self.deps(), 'Rowan Slate W8')

        @contextmanager
        def session(request, run):
            class Browser:
                def actions(self, listing):
                    return {'change-selection': 'Select variant at the reviewed shop'}

                def perform(self, listing, action_id, job, *, room):
                    events.append((action_id, job.run_id == run.run_id, room() > 0))
                    return listing, ReadResult(listing.source_id, status='failed',
                                               reason='Changed selection could not be confirmed.')
            try:
                yield Browser()
            finally:
                events.append('closed')

        deps.retailer_browser = session
        _, run = self.browse(deps, implementation='single_agent', requirements=dict(category='laptop'),
                             intent=dict(kind='exact_model', model='Rowan Slate W8'))
        self.assertEqual(run.status, 'abstained', self.trace(run))
        self.assertEqual(events, [('change-selection', True, True), 'closed'])
        self.assertFalse(run.recommendations or run.uncertain)
        prior, after = [json.loads(r['body']['messages'][1]['content']) for r in self.openrouter.requests[-2:]]
        self.assertEqual(len(prior['verified']), 1)
        self.assertFalse(after['verified'])

    def test_unpublished_charge_stays_partial(self):
        self.openrouter = Decisions(['Search serpapi', 'Inspect', 'Present'])
        offer = laptop('workbench', model='Rowan Slate W8', unknown_fee=True)
        deps = development_tools(self.deps(), 'Rowan Slate W8')
        deps.domestic_sources = Sources([], [offer])
        _, run = self.browse(deps, implementation='single_agent', requirements=dict(category='laptop'),
                             intent=dict(kind='exact_model', model='Rowan Slate W8'))
        self.assertEqual(run.status, 'partial', self.trace(run))
        self.assertFalse(run.recommendations)
        self.assertEqual(run.uncertain[0].cost.status, 'incomplete')
