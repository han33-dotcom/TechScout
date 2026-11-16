"""FastAPI application setup. Buyer workflow endpoints arrive with later MVP tasks."""
from contextlib import asynccontextmanager
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.storage import RUN_LEASE, Storage

log = logging.getLogger('techscout')
FRONTEND_DIST = Path(__file__).resolve().parent.parent / 'frontend' / 'dist'


def create_app(storage: Storage | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.storage = storage or Storage()
        # Only runs older than the lease: a command-line search may be running on the same database.
        recovered = app.state.storage.recover_interrupted_runs(live_for=RUN_LEASE)
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

    index = FRONTEND_DIST / 'index.html'
    if index.is_file():
        app.mount('/assets', StaticFiles(directory=FRONTEND_DIST / 'assets'), name='frontend-assets')

        @app.get('/', include_in_schema=False)
        def frontend():
            return FileResponse(index, headers={'Cache-Control': 'no-cache'})

    return app


app = create_app()
