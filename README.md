# Tech Scout

TechScout compares electronics from Lebanese sellers serving the buyer’s destination or pickup branch. FastAPI serves the React comparison workspace and owned run APIs. A separate buyer worker checks offers and publishes verified or uncertain results.

The [buyer product contract](docs/buyer-product-contract.md) defines the intended conversation, seller comparison, quotes and saved locations. The [acceptance matrix](docs/buyer-acceptance-matrix.md) maps all 18 scenarios to existing behavior, open work and required evidence.

**Current experiment:** [Single-agent comparison and results](docs/buyer-architecture-comparison.md) · [Task board](https://github.com/users/han33-dotcom/projects/2)

## Current implementation

The [architecture, API and reproducibility report](docs/architecture-api-report.md)
describes the domestic buyer and collection processes, measured coverage, API examples and remaining release checks.
The [delivery plan](docs/full-product-plan.md) defines the target product; it is not
an assertion that every planned feature is already operational.

- React + TypeScript + Vite provides request/filter entry, progress, clarification,
  comparison, refinement and private session history/export. Typed FastAPI endpoints
  persist owned runs and enforce session, CSRF and revision checks.
- The backend includes versioned source evidence, scoped full-text/vector retrieval,
  freshness, a retailer directory, shared resource accounting, bounded research
  workers, deterministic matching/cost checks, verification and grounded writing.
- The domestic buyer worker verifies retailer links, compares exact configurations
  across sellers and selects up to three models for needs-based requests. Start it
  with `python -m app.run_worker --controller app.buyer_flow:run` alongside the API.
  With no configured worker, buyer runs remain queued. [Buyer worker setup](docs/buyer-api.md)
- Class Cellulars, Mojitech and Beytech have existing readers/catalog helpers.
  Coverage is field/category-specific and includes documented live-access gaps.
  The [PC and Parts reader](docs/pcandparts.md) is validated with synthetic fixtures;
  its prior HTTP 403 remains unresolved and it is not activated as a live source.
- The v1 CLI and records remain available alongside the v2 workspace. They are not
  the complete five-category browser flow. See the [v1 specification](docs/mvp-spec.md)
  for legacy search limitations and the [v2 contracts](docs/full-product-contracts.md).

Live work requires the shared ledger described in [accounting](docs/shared-accounting.md).
The [local accounting setup](docs/local-accounting.md) records provisioning and historical charges.
Passing offline tests does not prove provider access, complete retailer coverage or
qualified buyer recommendations. Retailer permission emails are not a dependency;
the existing public-data assumption and technical source gates still apply.

## Run with Docker Compose

Use Docker Compose v2 from a fresh checkout.

~~~powershell
Copy-Item .env.example .env
# Set POSTGRES_PASSWORD in .env before starting. Keep provider keys blank for fixtures.
docker compose up --build --wait
docker compose exec app python -m app.storage load-fixtures
curl.exe http://127.0.0.1:8000/health
curl.exe http://127.0.0.1:8000/sources
docker compose exec db psql -U techscout -d techscout -c "SELECT '[1,2,3]'::vector <-> '[1,2,4]'::vector;"
~~~

On macOS or Linux use `cp .env.example .env` and `curl`. The distance query returns
`1`. Adjust the database user and name in the query if you override the defaults.
The buyer workspace is at `http://127.0.0.1:8000/`. `/health` checks the database.
`docker compose ps` shows the API, database and ingestion worker health. The worker
checks its database connection and access to the evidence volume. Job execution
arrives with the ingestion implementation.

Compose publishes localhost-only ports. Set `APP_PORT` or `DB_PORT` in `.env` if
the defaults are occupied. The pinned [pgvector image](https://github.com/pgvector/pgvector)
runs PostgreSQL 17. A one-shot migration service enables `vector` before the API and
worker start from the same application image. `docker compose down` preserves the
database and evidence volumes.

Keep OpenAI, Tavily and Firecrawl keys in the repository-root `.env` or backend
environment. Compose passes them only to the API and worker at runtime. Keep them
out of frontend env files, `VITE_` variables and backups. The workspace shell, health
checks and synthetic recovery fixture run without provider keys.

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

## Local development

Python 3.11 (the team and CI baseline). Run from the repository root:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m unittest discover -s tests -v
```

Without `DATABASE_URL`, the app and Alembic use a local SQLite file (`techscout.db`), which is convenient for quick experiments; Postgres is the supported database. To use the Compose database from the host, export `DATABASE_URL` in your shell (see `.env.example`; nothing reads it from `.env`), then:

```powershell
alembic upgrade head                      # apply migrations
python -m app.storage load-fixtures       # load config/sources.json and the synthetic contract example
$env:TECHSCOUT_COOKIE_SECURE = '0'         # local HTTP only; use 1 for HTTPS
uvicorn app.main:app --reload             # http://127.0.0.1:8000
```

For frontend development, use Node 22.12+ (Docker pins Node 24.14.1) and run `npm --prefix frontend ci`
then `npm --prefix frontend run dev`. Vite proxies `/health`, `/sources` and
`/api` to the FastAPI origin at `http://127.0.0.1:8000` by default. Set
`TECHSCOUT_API_ORIGIN` in the development shell if the API runs elsewhere.
`npm --prefix frontend run build` type-checks and bundles the assets into
`frontend/dist`. Restart the API to serve the built page and its assets under
the API origin. The Docker build performs these steps and copies only the
compiled assets into the Python runtime image.

To run one search by hand, with the migrations applied (a few public requests per store, stored as a run with its trace):

```powershell
python -m app.runs search examples/request-iphone17-beirut.json                 # rule baseline
python -m app.runs search examples/request-iphone17-beirut.json --planner none  # retrieval only
python -m app.runs search examples/request-iphone17-beirut.json --planner model # paid model planner
```

Each search first closes runs that a crash left `running`, once they started more than 10 minutes ago. The result prints as JSON. Press Ctrl+C once and wait: the run stops before its next step and the result prints. A second Ctrl+C aborts without a result and leaves the run `running` until recovery closes it, more than 10 minutes after it started. The command exits 0 when it prints a result, unless the run's status is `error`, and then it exits 1; an `error` run ends with `no store could be searched`, `no admitted source has a reader` or `run stopped on an unexpected error`. A run cut short by its own time or call budget ends `partial` with that limit's stop reason and exits 0, even if no store was searched, so check `source_coverage`. The command exits 1 without a result when the database cannot be used before the run starts, for example because migrations were not applied (`The run could not start: <Error>`); when the run stops on a storage failure after it was stored, for example because another process closed it (`Run <id> stopped on an unexpected <Error> without storing its result (stored status: <status>)`); and when even the stored status cannot be read (`Run <id> failed on an unexpected <Error>; whether it was stored could not be read`). It exits 2 before any store request when the request file cannot be read (`Cannot read the request: <Error>`), when the caps in `config/evaluation.json` cannot be read (`Cannot read the run caps in evaluation.json: <Error>`), or when the planner cannot be set up, for example `--planner model` without API credentials (`Cannot set up the model planner: <Error>`). `--planner none` runs the no-investigation baseline (`no-investigation` in the stored run) and, unless it ends in error, is cancelled or hits a run limit, ends with `investigation is disabled for this run`.

The legacy v1 CLI uses no exchange rates, so its request budget must match the retailer price currency. `--planner model` now uses direct OpenAI and requires backend `OPENAI_API_KEY` plus a configured shared accounting job; the default remains the rule baseline. Its model, price table and limits are pinned in `config/evaluation.json`. The old Anthropic implementation is retained for explicitly selected historical evaluations, not automatic fallback. See [model-provider configuration](docs/model-provider.md) and [shared accounting](docs/shared-accounting.md). Ordinary tests never contact retailers or paid providers.

To run the storage tests against Postgres as well, point `TEST_DATABASE_URL` at a disposable database (the tests reset its schema). CI runs both SQLite and Postgres, plus the Compose build and recovery rehearsal.

## Storage

Storage includes legacy sources/variants/offers/runs/actions, versioned public documents and observations, passages/vector indexes, retailer-directory records, accounting state and private buyer sessions/run snapshots. Rows retain queryable columns and validated contract JSON. v1 and v2 run lifecycles coexist; see [the architecture report](docs/architecture-api-report.md) for their boundaries.

- **Money** is stored exactly: `NUMERIC` without fixed scale on Postgres, a decimal string elsewhere. Floats are rejected.
- **Evidence** rows are immutable once stored and reference their raw response by SHA-256. Snapshots are written under `EVIDENCE_DIR` (owner-only permissions), verified against their hash on every read, and never served over HTTP.
- **Runs** left `running` when the service stops are marked `partial` with stop reason `interrupted: the service stopped before this run finished`. They are never resumed, so stale searches cannot silently continue. Recovery runs at web-app startup and at the start of each `python -m app.runs search`, and closes only runs that started more than 10 minutes ago (`RUN_LEASE` in `app/storage.py`): the web app and command-line searches share the database, so a younger run may still be live in another process. Recorded actions are kept. A recovered run's usage is its stored totals, which include requests and paid model calls in flight, because they are written before each is sent. Its `elapsed_ms` runs to its last recorded step, so it leaves out downtime and the step in flight at the crash; its `finished_at` is the time it was closed.

Schema changes go through a new Alembic revision in `migrations/versions/`; a test checks that the migrated schema matches `app/storage.py`.

## Sources

`app.source_registry.get_active_source(source_id)` rereads the technical source registry and requires an active unique source with review provenance, scoped HTTPS endpoints and positive operating limits. The legacy `admission` field contains the public-access assumption and technical review, not merchant permission.

See [evidence metadata](docs/source-evidence.json) for captured public responses and [synthetic offers](tests/fixtures/synthetic_offers.json) for optional offline failure fixtures. The [outreach drafts](docs/retailer-access-requests.md) are superseded and must not be sent.

## Team workspace

Use the [documentation index](docs/README.md) and [team workflow](docs/team-workflow.md).
Software tasks live in GitHub Issues and the project board; technical documents stay
in this repository. Meeting notes and general references belong in the Wiki after
publication. See [workspace activation](docs/github-workspace-activation.md) for the
current rollout status. Shared cloud AI review is optional and deferred.
