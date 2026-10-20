# Tech Scout

Exact-device shopping comparison for buyers in Lebanon. The planned MVP is one Python/FastAPI application, Postgres and a simple buyer interface; the backend foundation exists, the buyer interface is not built yet.

## Current implementation

MVP 01 is complete: [Class Cellulars, Mojitech and Beytech](docs/source-admission.md) are technically checked public sources, selected in [the configuration](config/sources.json). Public website data is assumed unrestricted for this project, as instructed by the user. Retailer permission emails are not a dependency.

MVP 02 defines the shared [request, offer and evidence contracts](docs/mvp-spec.md) in `app/contracts.py`.

MVP 03 adds the backend foundation: a FastAPI application (`app/main.py`), Postgres storage with Alembic migrations (`app/storage.py`, `migrations/`), protected evidence snapshots, Docker Compose and CI. Actual runtime retrieval and adapters are MVP 04–06; cost, eligibility and final verification are later tasks. The shopping workflow is not built yet.

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

To run the storage tests against Postgres as well, point `TEST_DATABASE_URL` at a disposable database (the tests reset its schema). CI runs both SQLite and Postgres, plus a Docker build.

## Storage

Six storage groups: sources, variants, offers, evidence (with raw-response snapshots), runs and actions. Rows keep queryable columns plus the validated contract JSON, so stored data always reloads into the MVP 02 contracts.

- **Money** is stored exactly: `NUMERIC` without fixed scale on Postgres, a decimal string elsewhere. Floats are rejected.
- **Evidence** rows are immutable once stored and reference their raw response by SHA-256. Snapshots are written under `EVIDENCE_DIR` (owner-only permissions), verified against their hash on every read, and never served over HTTP.
- **Runs** left `running` when the service stops are marked `partial` with an `interrupted` stop reason at the next startup. They are never resumed, so stale searches cannot silently continue. Recorded actions and budget usage are kept.

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
