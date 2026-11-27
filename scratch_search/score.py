"""Score a run with an independently supplied set of eligible seller/variant observations."""
import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path

from .offers import Observation, validate_claim


def offer_key(row):
    return (row["listing"]["shop"], row["listing"]["url"], row["storage_gb"],
            row["color"].casefold(), Decimal(row["amount"]))


def score(report_path, reference):
    run = json.loads(report_path.read_text(encoding="utf-8"))
    if (run["query"], run["market"]) != (reference["query"], reference["market"]):
        raise ValueError("Reference and run must describe the same request and market")
    shops = set(reference["shops"])
    if set(run["discovery"]) - shops:
        raise ValueError("The reference does not audit all participating shops")
    eligible = {offer_key(row) for row in reference["eligible"]}
    if any(key[0] not in shops for key in eligible):
        raise ValueError("Reference includes an unaudited shop")
    targets = {}
    for key in eligible:
        targets[key[2]] = min(key[-1], targets.get(key[2], key[-1]))
    correct = {key for key in eligible if key[-1] == targets[key[2]]}
    emitted = {offer_key(row) for row in run["winners"]}
    broken = []
    for row in run["winners"]:
        digest = row["capture"]
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            broken.append(digest)
            continue
        path = report_path.parent / "captures" / (digest + ".json")
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            broken.append(digest)
            continue
        state = json.loads(path.read_text(encoding="utf-8"))
        if (state["url"] != row["listing"]["url"] or state["time"] != row["observed_at"]
                or validate_claim(Observation.model_validate(row), state["text"], state["controls"])):
            broken.append(digest)
    return {"unsupported_winners": len(emitted - eligible), "missing_or_changed_winner_captures": broken,
            "correctly_won_capacities": len({key[2] for key in correct & emitted} - {key[2] for key in emitted - correct}),
            "answerable_capacities": len(targets), "missing_winning_offers_including_ties": len(correct - emitted),
            "correct_empty": (not emitted and run["status"] == "finished" and set(run["discovery"]) == shops)
                             if not eligible else None,
            "audited_shops": len(shops), "discovered_shops": len(run["discovery"]),
            "status": run["status"], "seconds": run["elapsed_seconds"], "cost_usd": run["cost_usd"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("reference", type=Path)
    args = parser.parse_args()
    print(json.dumps(score(args.report, json.loads(args.reference.read_text(encoding="utf-8"))), indent=2))
