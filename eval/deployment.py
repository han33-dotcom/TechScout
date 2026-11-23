"""Bind an isolated evaluation API and worker to the same strategy and product database."""
from functools import partial
import os
import re

from sqlalchemy.engine import make_url

from app.main import create_app
from app.inference import build_inference
from app.planner import load_evaluation_config
from app.storage import configured_database_url
from eval.runners import run_strategy


def binding():
    side = os.environ['TECHSCOUT_EVAL_SIDE']
    if side not in {'full-warm','full-cold','single-agent-warm','fixed-heuristic-warm'}:
        raise ValueError('Unknown evaluation side')
    name = make_url(configured_database_url()).database
    if not name or not re.fullmatch(r'techscout_eval_[a-f0-9]{32}',name):
        raise ValueError('Use a private trial database created by eval.session clone')
    return dict(side=side,database=name,corpus='cold' if side=='full-cold' else 'warm')


def pinned_inference(**kwargs):
    settings = load_evaluation_config().evaluation_inference
    if settings is None:
        raise ValueError('Missing frozen evaluation inference settings')
    return build_inference(settings, **kwargs)


def worker_controller(compose):
    """Inject the selected investigation strategy into the common end-to-end controller."""
    settings=binding()
    return compose(investigator=partial(run_strategy,settings['side']), inference=pinned_inference())


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
