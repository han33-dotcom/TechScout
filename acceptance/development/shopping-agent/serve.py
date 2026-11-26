"""Serve the built buyer UI on loopback for the two synthetic #200 development requests.

The model is live. All search and retailer responses are local development fixtures.
Requires the configured campaign ledger and backend OpenRouter key.
"""
import argparse
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
import uvicorn

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from app.accounting import Ledger
from app.buyer_flow import FlowDependencies, _code_version, execute
from app.buyer_runs import BuyerRunService
from app.main import create_app
from app.run_worker import execute_one
from app.shopping_agent import pinned_inference
from app.storage import Storage
from test_shopping_agent import development_tools


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-model', action='store_true', required=True)
    parser.add_argument('--traces', required=True, type=Path)
    parser.add_argument('--port', type=int, default=8790)
    args = parser.parse_args()
    ledger = Ledger.configured()
    if ledger.project != 'techscout-december-2026':
        parser.error('Use the frozen campaign ledger')
    inference = pinned_inference()
    with tempfile.TemporaryDirectory() as directory:
        url = f'sqlite:///{Path(directory) / "development.db"}'
        config = Config(str(ROOT / 'alembic.ini'))
        config.attributes['database_url'] = url
        command.upgrade(config, 'head')
        storage = Storage(url, Path(directory) / 'evidence')
        service = BuyerRunService(storage)
        deps = FlowDependencies(ledger=ledger, inference=inference, source=None, traces=args.traces,
            versions=dict(code=_code_version(), reasoning_effort='low', source_mode='development fixture'))

        def controller(context):
            model = getattr(context.request.intent, 'model', '')
            if context.request.implementation != 'single_agent' or model not in ('Aster Finch Q14', 'Rowan Slate W8'):
                return context.publish(status='error', stop_reason='Use one of the two development inputs.')
            research = SimpleNamespace(workers=SimpleNamespace())
            execute(context, development_tools(replace(deps, research=research), model))

        stop = threading.Event()
        def work():
            while not stop.wait(.2):
                execute_one(service, controller)

        worker = threading.Thread(target=work)
        worker.start()
        try:
            uvicorn.run(create_app(storage, buyer_service=service, cookie_secure=False),
                        host='127.0.0.1', port=args.port, log_level='warning')
        finally:
            stop.set()
            worker.join()


if __name__ == '__main__':
    main()
