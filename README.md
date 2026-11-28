# TechScout

TechScout helps buyers in Lebanon compare phones, laptops, tablets, monitors and audio products from Lebanese sellers. A buyer can name a model, paste a listing or describe what they need. TechScout answers with the matching offers, their current listed prices (before VAT and delivery) and links to the exact listings. The buyer completes the purchase on the retailer's site.

The implementation is the **scout agent** (`app/scout`). An LLM plans the search, reads the buyer's words and judges which listings match. Code reads each shop's own published data, checks every claim and handles the money. [Scout agent design](docs/scout-agent.md) · [Buyer product contract](docs/buyer-product-contract.md) · [Project board](https://github.com/users/han33-dotcom/projects/3)

**Current status.** The scout runs standalone from its CLI and a local demo page. It is being measured on a fresh set of evaluation requests, and will be connected to the buyer workspace, API and worker once it passes.

**Earlier implementations** are retired. They are kept only as baselines for comparison and inspection:
- [`baseline/pre-scout`](https://github.com/han33-dotcom/TechScout/tree/baseline/pre-scout): main before the cleanup, including the deterministic control flow, the single shopping agent and their evaluations.
- [`archive/test/two-agent`](https://github.com/han33-dotcom/TechScout/tree/archive/test/two-agent): the two-agent experiment.
- `archive/<branch>`: every other unmerged branch.

## Run the scout

The scout needs `OPENROUTER_API_KEY` in `.env`, `var/inference.json` (copy `config/inference.example.json`) and the shared ledger settings. See [OpenRouter](docs/openrouter.md) and the [shared ledger](docs/shared-accounting.md).

```sh
python -m app.scout "How much is a new Samsung Galaxy A56 5G with 256 GB?"
python -m app.scout.server                     # local demo page at http://127.0.0.1:8010
python eval/run_scout.py acceptance/scout/requests/development.json --out var/scout-eval/dev
```

Offline tests use synthetic shops and a scripted model:

```sh
python -m unittest discover -s tests -v
```

## Run the application stack

Docker Compose v2 starts Postgres, migrations, the API with the buyer workspace and the ingestion heartbeat. Copy `.env.example` to `.env` and set `POSTGRES_PASSWORD` first.

```sh
docker compose up --build --wait
docker compose exec app python -m app.storage load-fixtures
```

Open the [buyer workspace](http://127.0.0.1:8000/) or check [database health](http://127.0.0.1:8000/health). No search controller is installed until the scout is integrated, so submitted searches stay queued. [Local development and recovery](docs/local-development.md)

## Project guides

[Documentation index](docs/README.md) · [Contributing](CONTRIBUTING.md) · [Team workflow](docs/team-workflow.md) · [Agent rules](AGENTS.md)
