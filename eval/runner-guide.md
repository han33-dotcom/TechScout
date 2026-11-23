# Calibration and evaluation runners

Use the calibration cases during development. The harness checks the recorded protocol hashes and pre-run freeze before submitting a held-out case. #82 owns the measured study.

Run `python -m eval.scope` to inspect the domestic schedule offline. It lists the retired case IDs and planned sessions for each configuration. The four seeded ordering lists in `eval/baselines.json` retain the original relative order of domestic cases. The scorer rejects retired cases and mixed development/held-out inputs. For partial held-out results, its rates describe the submitted records. Check `heldout_scope.missing_automated_runs` for every configuration before publishing full-study rates. [Study population and reporting](../docs/evaluation-report.md)

For an automated trial, restore the chosen warm snapshot or prepared empty cold template with its source directory. With the local PostgreSQL administrator DSN in `TECHSCOUT_EVAL_ADMIN_DSN`, use `python -m eval.session clone TEMPLATE EVIDENCE_DIRECTORY TRIAL_OUTPUT`. Each call creates a distinct database and evidence directory. Set DATABASE_URL and EVIDENCE_DIR on both the evaluation API and the worker to those returned locations. Leave the shared accounting database configured independently. The worker must be the only buyer worker attached to the trial copy.

Set TECHSCOUT_EVAL_SIDE on both processes. Launch the API with `uvicorn --factory eval.deployment:api`. The worker composes the normal buyer controller with `eval.deployment.worker_controller(compose)`, where the controller factory takes investigator and inference keywords. Full trials invoke the orchestrator. The single-agent strategy chooses research actions sequentially and runs deterministic acquisition tools. The fixed baseline uses the existing RulePlanner. Every branch retains the same research scope and downstream cost and verification rules. The end-to-end controller and frozen warm snapshot are integration prerequisites for the measured study.

For example, send calibration case C01 to the chosen deployment with

    python -m eval.run_trial eval/calibration.json C01 fixed-heuristic-warm --origin http://localhost:8000 --output trial.json

The trace contains the actual request revision, owned snapshots and elapsed times. The harness uses protocol clarification answers and cancels at the wall-clock ceiling. Inspect trace events and ledger receipts when adjudicating offers and timing. Record a null time for an unreached display or verified result. Adjudicate no-match cases using all methods and the verifier pool.

For an external service or assistant session, generate the timed procedure and capture form first. For example

    python -m eval.session capture eval/calibration.json C01 lebtech
    python -m eval.session capture eval/calibration.json C01 chatgpt-browsing

Save the completed record with exact queries, visible configuration and terms, operator timestamps, source links and sanitized evidence. `python -m eval.session summarize RECORD.json` reports service time and extra verification effort. Access failures and unsupported features retain their own outcomes. External costs are unknown.

Save one adjudicated Trial from eval/scoring.py per line, referencing each raw trace and ledger receipt. Run

    python -m eval.scoring adjudicated.jsonl --acquisition-usd 0 --maintenance-usd 0 --pairs verified-pairs.jsonl

Replace example costs with measured corpus costs. Pair records contain case_id, side, same_case, market, equivalence, access_failure and the two PriceSide records (techscout and baseline). Record equivalent hardware using the identity decision or documented independent verification. Record confirmed condition, fulfillment basis, complete totals converted in one verification pass, warranty differences and evidence references. Fit for different models is scored separately. Missing decisive price evidence is inconclusive. Repetition-one validity, case-averaged success, abstention, censored timing percentiles and marginal run spend follow the frozen protocol. The scorer keeps corpus acquisition and maintenance costs separate.

