"""Bind an isolated evaluation API and worker to the same strategy and product database."""
from dataclasses import replace
import os
from pathlib import Path
import re

from sqlalchemy.engine import make_url

from app.assessment import EvidenceGap
from app.buyer_flow import production_dependencies
from app.domestic_buyer import DomesticBuyerFlow
from app.inference import TracedProvider, build_inference, load_env_file
from app.main import create_app
from app.planner import DEFAULT_CONFIG, PlannerAction, PlannerState, RulePlanner, load_evaluation_config
from app.storage import configured_database_url
from eval.freeze import digest


def binding():
    side = os.environ['TECHSCOUT_EVAL_SIDE']
    if side not in {'full-warm','full-cold','single-agent-warm','fixed-heuristic-warm'}:
        raise ValueError('Unknown evaluation side')
    url = make_url(configured_database_url())
    name = url.database
    if url.get_backend_name() != 'postgresql' or not name or not re.fullmatch(r'techscout_eval_[a-f0-9]{32}',name):
        raise ValueError('Use a private trial database created by eval.session clone')
    return dict(side=side,database=name,corpus='cold' if side=='full-cold' else 'warm')


def pinned_inference(**kwargs):
    settings = load_evaluation_config().evaluation_inference
    if settings is None:
        raise ValueError('Missing frozen evaluation inference settings')
    return build_inference(settings, **kwargs)


def trace_directory():
    return Path(os.environ['EVIDENCE_DIR']).resolve().parent / 'worker-traces'


class SequentialBuyer(DomesticBuyerFlow):
    """One sequential acquisition path, the shared verifier and a deterministic summary."""
    model_writer = False

    def evaluator_follow_up(self):
        # These controls have no separate evaluator choosing an extra read.
        return


class FixedBuyer(SequentialBuyer):
    def plan_search(self, options, channels):
        if not channels:
            return options[0], 'unavailable', 0
        gap = EvidenceGap(offer_id='catalog', source_id='domestic', field_path='seller_coverage',
                          reason=options[0].objective)
        actions = [PlannerAction(action_id=f'A{i}', tool='search_candidates', source_id=channel,
                                 settles=[gap.gap_id], calls=1) for i, channel in enumerate(channels)]
        state = PlannerState(request=self.request.requirements.model_dump(mode='json'), gaps=[gap],
                             actions=actions, turn=0, max_turns=1)
        choice = RulePlanner().decide(state)
        action = next(action for action in actions if action.action_id == choice.action_id)
        return replace(options[0], channel=action.source_id), 'rule-planner', 0


def run(context):
    """Controller entry point for one preconfigured trial database and study side."""
    settings = binding()
    side = settings['side']
    if not re.search(rf'-{re.escape(side)}-r[1-3]$', context.request.request_id):
        raise ValueError('Trial request and worker configuration differ')
    fixed = side == 'fixed-heuristic-warm'
    load_env_file()
    deps = production_dependencies(inference=None if fixed else pinned_inference(), load_worker_inference=False)
    try:
        deps.traces = trace_directory()
        deps.versions.update(evaluation=settings, inference_config_sha256=digest(DEFAULT_CONFIG))
        if side.startswith('full-'):
            flow = DomesticBuyerFlow(context, deps)
        else:
            deps.research_parallelism = 1
            if fixed:
                flow = FixedBuyer(context, deps)
                flow.trace['versions']['inference'] = dict(provider='none', strategy='rule-planner')
            else:
                flow = SequentialBuyer(context, deps)
                flow.roles = TracedProvider(deps.inference.roles, flow.calls, role='single_agent')
        flow.run()
    finally:
        deps.domestic_sources.storage.engine.dispose()
        deps.ledger.storage.engine.dispose()


def expose_binding(application, settings):
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def configured_side(request):
        return JSONResponse(settings)

    # The production app can already have its React fallback at /{path:path}.
    application.router.routes.insert(0, Route('/_evaluation/binding',configured_side,methods=['GET']))
    return application


def api():
    return expose_binding(create_app(), binding())
