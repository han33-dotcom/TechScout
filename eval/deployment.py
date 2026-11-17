"""Bind an isolated evaluation API and worker to the same strategy and product database."""
from functools import partial
import os
import re

from sqlalchemy.engine import make_url

from app.main import create_app
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


def worker_controller(compose):
    """Inject the selected investigation strategy into the common end-to-end controller."""
    settings=binding()
    return compose(investigator=partial(run_strategy,settings['side']))


def api():
    settings=binding()
    application=create_app()
    @application.get('/_evaluation/binding')
    def configured_side():
        return settings
    return application

