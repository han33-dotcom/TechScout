"""Check the pinned comparison inputs without running a buyer search."""
import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import closing
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FIELDS = {"id", "category", "entry", "mode", "destination", "prompt"}


def read(path):
    return json.loads(path.read_bytes())


def check(ok, message):
    if not ok:
        raise ValueError(message)


def load_freeze():
    manifest = read(HERE / "manifest-v2.json")
    original = HERE / "manifest-v1.json"
    digest = hashlib.sha256(original.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    check(manifest["replaces"]["sha256_lf"] == digest, "Original freeze digest differs")
    amended = {"version", "issue", "tools", "replaces"}
    previous = read(original)
    check({k: v for k, v in manifest.items() if k not in amended} ==
          {k: v for k, v in previous.items() if k not in amended},
          "Tool amendment changed another experiment condition")
    tools = manifest["tools"]
    check(tools["applies_to"] == ["control", "agent"], "Tool access must be shared")
    check(set(tools["web_discovery_enabled"]) == {"serpapi", "brave", "firecrawl", "manufacturer"}
          and set(tools["web_discovery_conditional"]) == {"tavily"}, "Discovery profile differs")
    check(tools["browser"]["enabled"] is True and tools["managed_scrape"] is True,
          "Browser interaction and managed reads must be enabled in the protocol")
    return manifest


def verify_catalog(path, catalog):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        head, = db.execute("SELECT version_num FROM alembic_version").fetchone()
        check(head == catalog["head"], "Migration revision differs")
        rows = [[key, json.loads(payload)] for key, payload in db.execute(
            "SELECT retailer_id, payload FROM directory_entries ORDER BY retailer_id"
        )]
        data = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        check(len(rows) == catalog["rows"], "Retailer count differs")
        check(hashlib.sha256(data.encode()).hexdigest() == catalog["directory_sha256"],
              "Directory content differs")
        for table, expected in catalog["empty"].items():
            check(table.isidentifier(), "Invalid table name")
            count, = db.execute(f'SELECT count(*) FROM "{table}"').fetchone()
            check(count == expected == 0, f"Starting state differs for {table}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, help="Read-only check of a freshly seeded SQLite database")
    args = parser.parse_args()
    manifest = load_freeze()
    for name, expected in manifest["sha256_lf"].items():
        data = (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
        check(hashlib.sha256(data).hexdigest() == expected, f"Hash differs for {name}")

    expected_first = {"control_first": [], "agent_first": []}
    prompts = []
    for name, cohort in manifest["cohorts"].items():
        buyers = read(ROOT / cohort["inputs"])
        ids = [row["id"] for row in buyers]
        check(ids == cohort["ids"] and len(set(ids)) == len(ids), f"{name} ID or order mismatch")
        check(all(set(row) == FIELDS for row in buyers), f"Unexpected buyer fields in {name}")
        prompts.extend(row["prompt"].casefold().strip() for row in buyers)
        expected_first["control_first"].extend(ids[::2])
        expected_first["agent_first"].extend(ids[1::2])
        reference = read(ROOT / cohort["grader"])
        check([row["id"] for row in reference["cases"]] == ids, f"{name} grader IDs differ")
        if name == "primary":
            check(buyers == [row["input"] for row in reference["cases"]], "Primary input drift")
            counts = Counter(row["expected_class"] for row in reference["cases"])
            check(counts == {"full": 10, "partial": 5, "blocked": 5}, "Primary class drift")
            check(sum(row["comparison_flag"] for row in reference["cases"]) == 5, "Pair flag drift")
        else:
            check(len(ids) == 6 and len({r["category"] for r in buyers}) == 5, "Fresh coverage drift")
            for case in reference["cases"]:
                check(case["evidence"] and set(case["evidence"]) <= reference["observations"].keys(),
                      f"Missing reference for {case['id']}")
                if case["verified_payable_usd"] is not None:
                    check(case["tax"] == "included" and Decimal(case["verified_payable_usd"]) ==
                          Decimal(case["price_usd"]) + Decimal(case["shipping_usd"]),
                          "Verified total is inconsistent")
    check(len(prompts) == len(set(prompts)) == 26, "Repeated prompt")
    for arm, ids in expected_first.items():
        check(manifest["order"][arm] == ids, f"Incorrect run order for {arm}")
    if args.catalog:
        verify_catalog(args.catalog, manifest["catalog"])
        print("Catalog matches the pinned migration, directory digest and empty tables.")
    print("Verified 20 primary and 6 fresh requests, grader separation, hashes and paired order.")


if __name__ == "__main__":
    main()
