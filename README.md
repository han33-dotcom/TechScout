# Tech Scout

Exact-device shopping comparison for buyers in Lebanon. The planned MVP is one Python/FastAPI application, Postgres and a simple buyer interface; those components are not built yet.

## Current implementation

MVP 01 is complete: [Class Cellulars, Mojitech and Beytech](docs/source-admission.md) are technically checked public sources, selected in [the configuration](config/sources.json). Public website data is assumed unrestricted for this project, as instructed by the user. Retailer permission emails are not a dependency.

The source selector and nine tests are implemented. Actual runtime retrieval and adapters are MVP 04–06; cost, eligibility and final verification are later tasks. Published product data is available, but the shopping workflow is not built yet.

```powershell
python -m unittest discover -s tests -v
```

Python 3.10+ and the standard library are sufficient for this task. Run from the repository root; no extra packages or credentials are needed.

`app.source_registry.get_active_source(source_id)` rereads the technical source registry and requires an active unique source with review provenance, scoped HTTPS endpoints and positive operating limits. The legacy `admission` field contains the public-access assumption and technical review, not merchant permission.

See [evidence metadata](docs/source-evidence.json) for captured public responses and [synthetic offers](tests/fixtures/synthetic_offers.json) for optional offline failure fixtures. The [outreach drafts](docs/retailer-access-requests.md) are superseded and must not be sent.
