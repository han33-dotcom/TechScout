# Local development and recovery

Install Python 3.11 dependencies using the [contributor guide](../CONTRIBUTING.md). Activate that environment and run commands from the repository root. Set `DATABASE_URL` in the shell for PostgreSQL. The default is a local SQLite file.

```powershell
alembic upgrade head
python -m app.storage load-fixtures
$env:TECHSCOUT_COOKIE_SECURE = '0'  # loopback HTTP only
uvicorn app.main:app --reload
```

Start the [buyer worker](buyer-api.md) against the same database to process searches. Live searches require the [shared ledger](shared-accounting.md) and backend provider configuration. Keep keys out of frontend environment files, `VITE_` variables and backups.

## Frontend

Use Node 22.12 or newer. Run `npm --prefix frontend ci` and `npm --prefix frontend run dev`. Vite proxies API requests to `http://127.0.0.1:8000`. Set `TECHSCOUT_API_ORIGIN` for another origin. To serve bundled assets through FastAPI, run `npm --prefix frontend run build` and restart the API.

## Database and evidence

Compose publishes localhost-only ports. Change `APP_PORT` or `DB_PORT` in `.env` if needed. The migration service enables pgvector before starting the API and heartbeat worker. `docker compose down` preserves both volumes. Set `TEST_DATABASE_URL` only to a disposable database because the tests reset its schema.

New schema revisions belong in `migrations/versions/`. Money uses exact decimal storage. Evidence snapshots under `EVIDENCE_DIR` are private and verified by SHA-256 on read.

### Back up and restore

Use Python 3.11 and Docker Compose. Stop the API and worker and finish any host
CLI writes before taking a paired PostgreSQL dump and evidence archive. Keep
both files in a private directory outside version control.

~~~powershell
docker compose stop app worker
python scripts/local_recovery.py backup var/backup
docker compose up --wait
~~~

To recover, choose a fresh Compose project name and use unused `DB_PORT` and
`APP_PORT` values, or stop the original stack first. Start only `db` before the
restore, so the target has empty volumes. Use the trusted archive files created
by the backup command.

~~~powershell
docker compose -p techscout-restored build app
docker compose -p techscout-restored up --wait db
python scripts/local_recovery.py restore var/backup --project techscout-restored
docker compose -p techscout-restored up --wait
docker compose -p techscout-restored ps
~~~

If restore fails after writing either volume, use a fresh project for the retry.
The original data and archive are available throughout the recovery. For an
existing `postgres:17-alpine` deployment, build the new app image and take the
paired backup while the old database container is still running. Restore into
a fresh pgvector project for the image transition. The migration service
then upgrades the restored database from its existing Alembic revision.

Run `python scripts/check_local_stack.py` to rehearse with two disposable Compose
projects and synthetic records. It checks the bundled assets, health, fixtures,
vector query, restart and clean restore. Restored data includes an exact Decimal
price, hash-verified evidence and retained trace and usage for an interrupted run.
