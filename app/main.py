"""FastAPI application setup. Buyer workflow endpoints arrive with later MVP tasks."""
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.storage import Storage

log = logging.getLogger('techscout')


def create_app(storage: Storage | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.storage = storage or Storage()
        recovered = app.state.storage.recover_interrupted_runs()
        if recovered:
            log.warning('Marked %d interrupted run(s) as partial: %s', len(recovered), recovered)
        yield
        app.state.storage.engine.dispose()

    app = FastAPI(title='Tech Scout', version='0.1.0', lifespan=lifespan)

    @app.get('/health')
    def health():
        try:
            app.state.storage.ping()
        except Exception:
            log.exception('Database health check failed')
            return JSONResponse({'status': 'error', 'database': 'unavailable'}, status_code=503)
        return {'status': 'ok', 'database': 'ok'}

    @app.get('/sources')
    def active_sources():
        # Operational summary only; evidence snapshots are never served over HTTP.
        return [{'id': s.id, 'name': s.name, 'origin': str(s.origin)}
                for s in app.state.storage.list_sources(status='active')]

    return app


app = create_app()
