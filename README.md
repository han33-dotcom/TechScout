# TechScout

TechScout helps buyers compare phones, laptops, tablets, monitors and audio products from Lebanese sellers. A buyer can name a model, paste a listing or describe what they need. The [buyer contract](docs/buyer-product-contract.md) defines how the assistant should advise them and what evidence an offer needs.

**Current status.** The first implementation found three correct listing pages on five fresh requests and produced no delivered totals. The next experiment compares it with a single shopping agent on the same requests. [Experiment results and tasks](docs/buyer-architecture-comparison.md) · [Project board](https://github.com/users/han33-dotcom/projects/2)

## Run locally

From the repository root, copy `.env.example` to `.env` and set `POSTGRES_PASSWORD`. Docker Compose v2 starts the database, migrations, API and ingestion heartbeat.

```sh
docker compose up --build --wait
docker compose exec app python -m app.storage load-fixtures
```

Open the [buyer workspace](http://127.0.0.1:8000/) or check [database health](http://127.0.0.1:8000/health). A separate buyer worker processes searches. Without it, submissions stay queued. [Worker setup](docs/buyer-api.md) · [Host setup and backup](docs/local-development.md)

## Project guides

- [Product scope and submission](docs/full-product-plan.md) · [Buyer acceptance scenarios](docs/buyer-acceptance-matrix.md)
- [Architecture and API](docs/architecture-api-report.md) · [Documentation index](docs/README.md)
- [Contributing and offline tests](CONTRIBUTING.md) · [Team workflow](docs/team-workflow.md)
