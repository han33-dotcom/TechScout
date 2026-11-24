"""Check the published study against its frozen inputs and original run order."""
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import gzip
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVAL = HERE.parents[1]


def load(path):
    return json.loads(path.read_bytes())


def scheduled(baselines):
    sides = ["full-warm", "single-agent-warm", "fixed-heuristic-warm"]
    block = 0
    for rep in (1, 0, 2, 3):
        if rep == 0:
            for case in baselines["run_order"]["cold"]:
                yield case, "full-cold", 1
            continue
        for case in baselines["run_order"][f"warm_repetition_{rep}"]:
            offset = block % 3
            order = sides[offset:] + sides[:offset]
            block += 1
            if rep == 1 and case in baselines["paired_sessions"]["orders"]:
                order.remove("full-warm")
                order.insert(0, "full-warm")
            for side in order:
                yield case, side, rep


def verify():
    if not __debug__:
        raise RuntimeError("Run the evidence verifier with Python assertions enabled.")
    freeze = load(EVAL / "freeze.json")
    for section in (freeze, freeze["pre_run"]):
        for name, expected in section["files"].items():
            data = (EVAL.parent / name).read_bytes().replace(b"\r\n", b"\n")
            assert sha256(data).hexdigest() == expected, name
    config = load(EVAL / "run-freeze.json")
    baseline = load(EVAL / "baselines.json")
    expected_order = list(scheduled(baseline))
    with gzip.open(HERE / "trials.jsonl.gz", "rt", encoding="utf-8") as stream:
        rows = sorted(map(json.loads, stream), key=lambda r: r["started_at"])
    observed_order = [(r["case_id"], r["side"], r["repetition"]) for r in rows]
    assert (
        observed_order == expected_order
    ), "The submitted slots or their order differ from the original plan"
    assert len({r["run_id"] for r in rows}) == len(rows)
    assert len({r["deployment"]["database"] for r in rows}) == len(rows)
    freeze_time = datetime.fromisoformat(freeze["pre_run"]["recorded_at"])
    for row in rows:
        assert datetime.fromisoformat(row["started_at"]) >= freeze_time
        assert row["worker"]["versions"]["evaluation"] == row["deployment"]
        assert row["worker"]["versions"]["code"] == config["code_commit"]
        assert row["worker"]["run"] == row["trace"][-1]["snapshot"]["run"]
        assert row["contract_audit"]["input_matches_first_api_snapshot"]
        assert not row["contract_audit"]["changed_request_fields_across_polls"]
        assert not any(call.get("usage", {}).get("response_id") for call in row["worker"]["model_calls"])
        assert not any(
            attempt.get("request_id")
            for channel in row["worker"].get("discovery_channels", [])
            for attempt in channel["attempts"]
        )
        assert row["ledger_receipts"], row["run_id"]
    decisions = load(HERE / "adjudications.json")
    assert set(decisions) == {r["case_id"] for r in rows}
    assert all(v["sources"] and v["observation_notes"] for v in decisions.values())
    for decision in decisions.values():
        for source in decision["sources"]:
            if source.get("screenshot"):
                data = (HERE / source["screenshot"]).read_bytes()
                assert sha256(data).hexdigest() == source["screenshot_sha256"]
    ext = load(HERE / "external.json")
    expected_ext = {
        (case, side)
        for side, subset in [
            ("lebtech", "local_external"),
            ("onesouq", "local_external"),
            ("chatgpt-browsing", "assistant"),
            ("claude-browsing", "assistant"),
        ]
        for case in baseline["subsets"][subset]
    }
    assert len(ext) == len(expected_ext) and {(r["case_id"], r["side"]) for r in ext} == expected_ext
    for r in ext:
        if r["submitted"]:
            assert r["queries"] and datetime.fromisoformat(r["started_at"]) >= freeze_time
            assert all((HERE / path).is_file() for path in r["screenshots"])
        else:
            assert not r.get("queries") and r["outcome"] == "unmeasured"
    ledger = load(HERE / "ledger.json")
    assert ledger["evaluation_run_ids_with_job_receipts"] == len(rows)
    assert ledger["study_automated_trials_recorded"] == len(rows)
    assert Decimal(ledger["project_used_including_reservations_usd"]) <= Decimal(
        ledger["project_ceiling_usd"]
    )
    print(
        f"{len(rows)} original automated slots, {len(decisions)} case records, {len(ext)} external slots, "
        f"{sum(r['submitted'] for r in ext)} external submissions. Freeze, order, bindings and ledger verified."
    )


if __name__ == "__main__":
    verify()
