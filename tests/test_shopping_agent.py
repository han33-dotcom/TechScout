"""Separate development inputs. No scored requests or evaluation evidence are loaded."""
from app.domestic_buyer_sources import ReadResult
from app.domestic_discovery import ChannelAttempt, SearchReport
from test_buyer_flow import FakeOpenRouter, FlowCase
from test_domestic_buyer import Sources, laptop, lead


class DevelopmentListings(Sources):
    def read(self, request, url, job, run, *, earlier, room):
        if url not in self.current:
            return ReadResult('old-shop', status='failed', reason='The listing has been removed.')
        return super().read(request, url, job, run, earlier=earlier, room=room)


class DevelopmentSearch:
    connectors = {'serpapi': object()}

    def __init__(self, offer):
        self.offer = offer

    def search(self, query, job, *, alternatives):
        url = str(self.offer.listing_url)
        if self.offer.variant.product.model == 'Aster Finch Q14' and 'price' not in query.query:
            return SearchReport(job.run_id, (ChannelAttempt('serpapi', 'ok'),),
                                (lead('https://old-shop.example/discontinued', self.offer),))
        return SearchReport(job.run_id, (ChannelAttempt('serpapi', 'ok'),), (lead(url, self.offer),))


def development_tools(deps, model):
    offer = laptop('workbench', model=model, price='712.35')
    deps.domestic_sources = DevelopmentListings([], [offer])
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
        index = next(i for i, label in enumerate(data['available']) if label.startswith(prefix))
        return dict(action_index=index, offer_ids=[v['offer_id'] for v in data['verified']],
                    question='Which configuration do you need?' if prefix == 'Ask' else None,
                    material_reason='Memory changes the available models.' if prefix == 'Ask' else None)


class ShoppingAgentTests(FlowCase):
    def agent(self, model, ram, sequence):
        self.openrouter = Decisions(sequence)
        deps = development_tools(self.deps(), model)
        return self.browse(deps, implementation='single_agent',
            requirements=dict(category='laptop', min_ram_gb=ram),
            intent=dict(kind='exact_model', model=model, wording=f'Find {model} for delivery'),
            refinement_note='Compare all mandatory fees.')[1]

    def test_recover_after_removed_listing(self):
        run = self.agent('Aster Finch Q14', 16, ['Search serpapi', 'Inspect', 'Search serpapi',
                                                 'Inspect', 'Present'])
        self.assertEqual(run.status, 'complete', self.trace(run))
        self.assertEqual(str(run.recommendations[0].primary.cost.total.amount), '722.35')
        trace = self.trace(run)['actions']
        self.assertEqual([a.get('status') for a in trace], ['ok', 'failed', 'ok', 'ok', None])

    def test_wrong_selected_memory_is_rejected(self):
        run = self.agent('Rowan Slate W8', 32, ['Search serpapi', 'Inspect', 'Present'])
        self.assertEqual(run.status, 'abstained', self.trace(run))
        self.assertFalse(run.recommendations or run.uncertain)
        self.assertIn('RAM requirement (listing has 16 GB)', run.stop_reason)
        self.assertIn('Rowan Slate W8', run.stop_reason)

    def test_question_and_feedback_reach_resumed_agent(self):
        run = self.agent('Rowan Slate W8', 32, ['Ask'])
        self.assertEqual(run.status, 'clarification', self.trace(run))
        response = self.client.post(f'/api/runs/{run.run.run_id}/clarify', headers=self.headers,
            json=dict(run=run.run.model_dump(), clarification_id=run.clarification.clarification_id,
                      answer='I can only use the 32 GB configuration.'))
        self.assertEqual(response.status_code, 202)
        self.openrouter = Decisions(['Present'])
        self.work(development_tools(self.deps(), 'Rowan Slate W8'))
        import json
        data = json.loads(self.openrouter.requests[-1]['body']['messages'][1]['content'])
        self.assertEqual(data['request']['intent']['wording'], 'Find Rowan Slate W8 for delivery')
        self.assertEqual(data['request']['refinement_note'], 'Compare all mandatory fees.')
        self.assertEqual(data['feedback'][0]['question'], run.clarification.question)
        self.assertIn('32 GB', data['feedback'][0]['answer'])
