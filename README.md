# Tech Scout

Exact-device shopping comparison for buyers in Lebanon. The planned MVP is one Python/FastAPI application, Postgres and a simple buyer interface; the backend foundation exists, the buyer interface is not built yet.

## Current implementation

MVP 01 is complete: [Class Cellulars, Mojitech and Beytech](docs/source-admission.md) are technically checked public sources, selected in [the configuration](config/sources.json). Public website data is assumed unrestricted for this project, as instructed by the user. Retailer permission emails are not a dependency.

MVP 02 defines the shared [request, offer and evidence contracts](docs/mvp-spec.md) in `app/contracts.py`.

MVP 03 adds the backend foundation: a FastAPI application (`app/main.py`), Postgres storage with Alembic migrations (`app/storage.py`, `migrations/`), protected evidence snapshots, Docker Compose and CI.

MVP 04 adds the source gate (`app/sources.py`), which every store request goes through: GET only, to an allow-listed endpoint of an admitted source, within per-source limits and the run budget.

MVP 05–06 add store readers for the three sources (`app/adapters/`), with a [coverage matrix](docs/source-coverage.md) of what each store publishes. MVP 07–09 add the fixed rules: variant identity and deduplication (`app/identity.py`), buyer-constraint eligibility (`app/eligibility.py`) and evidence-bound totals (`app/costs.py`). MVP 11 adds run control with budgets and traces (`app/runs.py`, `app/tracing.py`) and MVP 12 the bounded planner (`app/planner.py`, `config/evaluation.json`). See [Shopping workflow](docs/mvp-spec.md#shopping-workflow). Indexed discovery (MVP 10), final re-verification (MVP 13) and the buyer interface are not built yet: a search reads one configured iPhone 17 product per store. No store currently states device condition, so today's searches end with candidates and open doubts rather than recommendations.

## Run with Docker Compose (Postgres)

From a fresh checkout:

```powershell
Copy-Item .env.example .env          # then set POSTGRES_PASSWORD (and DATABASE_URL) in .env
docker compose up -d --build         # starts Postgres, runs migrations, serves on 127.0.0.1:8000
docker compose exec app python -m app.storage load-fixtures
curl http://127.0.0.1:8000/health    # {"status":"ok","database":"ok"}
curl http://127.0.0.1:8000/sources   # active retailer sources
```

If ports 5432 or 8000 are taken, set `DB_PORT`/`APP_PORT` in `.env` (and use that port in `DATABASE_URL` and the URLs above). The app container runs `alembic upgrade head` before starting. `docker compose down` stops the stack; add `-v` to delete the database and evidence volumes.

## Local development

Python 3.11 (the team and CI baseline). Run from the repository root:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m unittest discover -s tests -v
```

Without `DATABASE_URL`, the app and Alembic use a local SQLite file (`techscout.db`), which is convenient for quick experiments; Postgres is the supported database. To use the Compose database from the host, set `DATABASE_URL` from `.env.example`, then:

```powershell
alembic upgrade head                      # apply migrations
python -m app.storage load-fixtures       # load config/sources.json and the synthetic contract example
uvicorn app.main:app --reload             # http://127.0.0.1:8000
```

To run one search by hand, with the migrations applied (a few public requests per store, stored as a run with its trace):

```powershell
python -m app.runs search examples/request-iphone17-beirut.json                 # rule baseline
python -m app.runs search examples/request-iphone17-beirut.json --planner none  # retrieval only
python -m app.runs search examples/request-iphone17-beirut.json --planner model # paid model planner
```

Each search first closes runs that a crash left `running`, once they started more than 10 minutes ago. The result prints as JSON; one Ctrl+C cancels the run. A run in which no store could be searched ends with status `error` and stop reason `no store could be searched`, and the command exits 1. If a run stops on a storage failure after it was stored (for example, another process closed it), the command prints `Run <id> stopped on an unexpected <Error> without storing its result (stored status: <status>)` instead of a result and exits 1. `--planner none` runs the no-investigation baseline (`no-investigation` in the stored run) and ends with `investigation is disabled for this run`.

Runs use no exchange rates, so give the budget in USD, the price currency of every admitted store; with a budget in another currency, no offer can be verified. The model planner needs `ANTHROPIC_API_KEY` exported in the shell that runs the command (nothing reads it from `.env`, and Compose does not pass it to the container) and spends at most the run's spend cap (USD 1.00). The ordinary test suite never contacts a store.

To run the storage tests against Postgres as well, point `TEST_DATABASE_URL` at a disposable database (the tests reset its schema). CI runs both SQLite and Postgres, plus a Docker build.

## Storage

Six storage groups: sources, variants, offers, evidence (with raw-response snapshots), runs and actions. Rows keep queryable columns plus the validated contract JSON, so stored data always reloads into the MVP 02 contracts.

- **Money** is stored exactly: `NUMERIC` without fixed scale on Postgres, a decimal string elsewhere. Floats are rejected.
- **Evidence** rows are immutable once stored and reference their raw response by SHA-256. Snapshots are written under `EVIDENCE_DIR` (owner-only permissions), verified against their hash on every read, and never served over HTTP.
- **Runs** left `running` when the service stops are marked `partial` with an `interrupted` stop reason. They are never resumed, so stale searches cannot silently continue. Recovery runs at web-app startup and at the start of each `python -m app.runs search`, and closes only runs that started more than 10 minutes ago (`RUN_LEASE` in `app/storage.py`): the web app and command-line searches share the database, so a younger run may still be live in another process. Recorded actions are kept. A recovered run's usage is its stored totals, which include requests and paid model calls in flight, because they are written before each is sent. Its `elapsed_ms` runs to its last recorded step, so it leaves out downtime and the step in flight at the crash; its `finished_at` is the time it was closed.

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
