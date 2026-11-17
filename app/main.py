"""FastAPI application setup and same-origin buyer workflow API."""
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.storage import RUN_LEASE, Storage
from app.buyer_api import BodyLimit, router
from app.buyer_runs import BuyerRunService, RunError

log = logging.getLogger('techscout')
FRONTEND_DIST = Path(__file__).resolve().parent.parent / 'frontend' / 'dist'


def create_app(storage: Storage | None = None, *, buyer_service=None, cookie_secure=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.storage = storage or Storage()
        app.state.buyer_runs = buyer_service or BuyerRunService(app.state.storage)
        app.state.buyer_runs.recover()
        # Only runs older than the lease: a command-line search may be running on the same database.
        recovered = app.state.storage.recover_interrupted_runs(live_for=RUN_LEASE)
        if recovered:
            log.warning('Marked %d interrupted run(s) as partial: %s', len(recovered), recovered)
        yield
        app.state.storage.engine.dispose()

    app = FastAPI(title='Tech Scout', version='0.1.0', lifespan=lifespan)
    app.add_middleware(BodyLimit)
    secure = os.environ.get('TECHSCOUT_COOKIE_SECURE','1')!='0' if cookie_secure is None else cookie_secure
    app.include_router(router(cookie_secure=secure))

    @app.exception_handler(RunError)
    async def run_error(request,exc):
        return JSONResponse({'detail':exc.detail},status_code=exc.status,headers={'Cache-Control':'no-store'})

    @app.middleware('http')
    async def private_responses(request,call_next):
        response=await call_next(request)
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control']='no-store'
        return response

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

        @app.get('/{path:path}',include_in_schema=False)
        def frontend_route(path:str,request:Request):
            if (path.split('/')[0] in {'api','assets','health','sources','docs','redoc','openapi.json','evidence'}
                    or '.' in path.rsplit('/',1)[-1] or 'text/html' not in request.headers.get('accept','')):
                raise HTTPException(404,'Not found')
            return FileResponse(index,headers={'Cache-Control':'no-cache'})

    return app


app = create_app()
