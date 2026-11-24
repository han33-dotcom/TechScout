# Calibration and evaluation runners

## First paired session

Record Codex as operator and as the agent performing direct retailer rechecks. Record the signed-in browsing plans. For an immediate start, leave `schedule.first_block_planned_at` null and run `python eval/freeze.py --pre-run --start-now`. The runner records an offset-aware timestamp before hashing the settings.

The first paired case is H15 after the opening H23 automated block. On H15 run TechScout, One Souq and LebTech in that order. Record both consumer assistant slots as unmeasured. Each interface receives only its frozen inputs. Save the response, timing and dated retailer checks with the operator identity. Preserve unknown charges, blocked access and missing evidence. The ten human-shopping sessions and independent human judgments from the earlier protocol remain unmeasured. [Current procedures](../docs/evaluation-protocol.md)

Use the calibration cases during development. The harness checks the recorded protocol hashes and pre-run freeze before submitting a held-out case. #82 owns the measured study.

Run `python -m eval.scope` to inspect the domestic schedule offline. It lists the retired case IDs and planned sessions for each configuration. The four seeded ordering lists in `eval/baselines.json` retain the original relative order of domestic cases. The scorer rejects retired cases and mixed development/held-out inputs. For partial held-out results, its rates describe the submitted records. Check `heldout_scope.missing_automated_runs` for every configuration before publishing full-study rates. [Study population and reporting](../docs/evaluation-report.md)

For an automated trial, restore the chosen warm snapshot or prepared empty cold template with its source directory. With the local PostgreSQL administrator DSN in `TECHSCOUT_EVAL_ADMIN_DSN`, use `python -m eval.session clone TEMPLATE EVIDENCE_DIRECTORY TRIAL_OUTPUT`. Each call creates a distinct database and evidence directory. Set DATABASE_URL and EVIDENCE_DIR on both the evaluation API and the worker to those returned locations. Leave the shared accounting database configured independently. The worker must be the only buyer worker attached to the trial copy.

Set `TECHSCOUT_EVAL_SIDE` on the API and worker. Start the API with `uvicorn --factory eval.deployment:api` and the worker with `python -m app.run_worker --controller eval.deployment:run`. Set `EVIDENCE_DIR` on the trial runner to the same clone's evidence directory. For loopback HTTP, set `TECHSCOUT_COOKIE_SECURE=0` on the API so the browser can return its session cookie. Use the default secure cookie for HTTPS.

The full configurations use the domestic buyer's AI search planner, up to three concurrent readers, the evidence evaluator and the writer. The single-agent configuration uses the same pinned model to select searches and assess needs, with sequential reads and a deterministic summary. The fixed configuration selects the first eligible channel via `RulePlanner`, uses the default product query and produces a deterministic summary. It can verify exact products and structured constraints. A free-text suitability judgment that needs inference stays unresolved. All four configurations share the source gate, ledger, costs and final verifier.

For example, send calibration case C01 to the chosen deployment with

    python -m eval.run_trial eval/calibration.json C01 fixed-heuristic-warm --origin http://localhost:8000 --output trial.json

The trace contains the actual request revision, owned snapshots and elapsed times. The worker saves its trace under `worker-traces` next to the cloned evidence directory. The runner attaches it after verifying the run revision, database, corpus and side against the API. Missing or mismatched worker evidence is recorded as a trial error. The harness uses protocol clarification answers and cancels at the wall-clock ceiling. Inspect trace events and ledger receipts when adjudicating offers and timing. Record a null time for an unreached display or verified result. Adjudicate no-match cases using all methods and the verifier pool.

For an external service or assistant session, generate the timed procedure and capture form first. For example

    python -m eval.session capture eval/calibration.json C01 lebtech
    python -m eval.session capture eval/calibration.json C01 chatgpt-browsing

Save the completed record with exact queries, visible configuration and terms, operator timestamps, source links and sanitized evidence. `python -m eval.session summarize RECORD.json` reports service time and extra verification effort. Access failures and unsupported features retain their own outcomes. External costs are unknown.

Save one adjudicated Trial from eval/scoring.py per line, referencing each raw trace and ledger receipt. Run

    python -m eval.scoring adjudicated.jsonl --acquisition-usd 0 --maintenance-usd 0 --pairs verified-pairs.jsonl

Replace example costs with measured corpus costs. Pair records contain case_id, side, same_case, market, equivalence, access_failure and the two PriceSide records (techscout and baseline). Record equivalent hardware using the identity decision or documented independent verification. Record confirmed condition, fulfillment basis, complete totals converted in one verification pass, warranty differences and evidence references. Fit for different models is scored separately. Missing decisive price evidence is inconclusive. Repetition-one validity, case-averaged success, abstention, censored timing percentiles and marginal run spend follow the frozen protocol. The scorer keeps corpus acquisition and maintenance costs separate.

