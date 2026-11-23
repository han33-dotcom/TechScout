# Evaluation protocol files

This folder holds the frozen held-out evaluation protocol from
[#78](https://github.com/han33-dotcom/TechScout/issues/78). The rules are explained in
[docs/evaluation-protocol.md](../docs/evaluation-protocol.md). Nothing here is a result.

- `heldout.json`: the original 30 held-out requests. The schedule includes the 23 domestic cases. Seven international cases are retained as an archive. Do not run, tune on, demo or ingest them before the pre-run freeze.
- `calibration.json`: 13 separate development cases for calibration and scorer checks.
- `protocol.json`: metrics, denominators, verdicts, comparability, timing and statistics.
- `baselines.json`: configurations, subsets, repetitions, run order and timed procedures.
- `models.json`: frozen model access for the main evaluation.
- `corpus/`: the cold manifest and the warm manifest template.
- `run-freeze.template.json`: settings to record before the first held-out run.
- `freeze.json` and `freeze.py`: the version and content hashes, and the tool that checks them.

Check that nothing frozen has changed:

```sh
python eval/freeze.py
```

Never copy held-out cases, their texts or any output produced for them into the corpus, app
fixtures, UI examples or development sets.
