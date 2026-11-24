"""Reproduce the measured counts from the published original traces and adjudications."""
from collections import Counter, defaultdict
from decimal import Decimal
import gzip
import json
from math import ceil
from pathlib import Path
from random import Random
from statistics import median, quantiles

ROOT = Path(__file__).resolve().parent
STAGES = ("discovered", "attempted", "retrieved", "qualifying")


def distribution(values):
    if not values:
        return None
    values = sorted(values)
    q1, _, q3 = quantiles(values, method="inclusive") if len(values) > 1 else values * 3
    return dict(
        min=values[0],
        q1=q1,
        median=median(values),
        q3=q3,
        p95=values[ceil(0.95 * len(values)) - 1],
        max=values[-1],
    )


def censored(values):
    observed = sorted(x for x in values if x is not None)

    def point(q):
        index = ceil(q * len(values)) - 1
        return observed[index] if 0 <= index < len(observed) else None

    return dict(observed=len(observed), total=len(values), p50=point(0.5), p95=point(0.95))


def clustered(groups):
    """Each case contributes one fraction. Resample cases with the protocol seed."""
    values = [sum(rows) / len(rows) for _, rows in sorted(groups.items()) if rows]
    if not values:
        return dict(cases=0, rate=None, ci95=None)
    point = sum(values) / len(values)
    if len(set(values)) == 1:
        return dict(cases=len(values), rate=point, ci95=[point, point])
    rng = Random(78)
    estimates = sorted(sum(rng.choices(values, k=len(values))) / len(values) for _ in range(10000))
    return dict(cases=len(values), rate=point, ci95=[estimates[249], estimates[9749]])


def by_case(records, field):
    groups = defaultdict(list)
    for row in records:
        if row[field] is not None:
            groups[row["case_id"]].append(int(row[field]))
    return clustered(groups)


def measure(row, decisions):
    case_id = row["case_id"]
    case = decisions.get(case_id, {})
    worker = row.get("worker", {})
    frames = row.get("trace", [])
    final = frames[-1]["snapshot"] if frames else {}
    groups = final.get("recommendations", [])
    observed = bool(frames)
    sources = final.get("source_outcomes", [])
    receipts = row["ledger_receipts"]
    dispatched = {
        a["source"]
        for a in receipts
        if a["kind"] == "retrieval" and a["status"] in {"sent", "completed", "uncertain"} and a["source"]
    }
    qualifying = {
        offer["offer"]["retailer_id"]
        for group in groups
        for offer in [group["primary"], *group["additional_sellers"]]
    }
    registered = json.loads((ROOT / "directory-identities.json").read_bytes())
    origins = {host: seller for seller, hosts in registered.items() for host in hosts}
    candidates = {s["source_id"] for s in sources}
    known = {s for s in candidates if s in registered}
    known.update(
        origins[s[5:].removeprefix("www.")]
        for s in candidates
        if s.startswith("lead:") and s[5:].removeprefix("www.") in origins
    )
    stages = dict(
        discovered=known,
        attempted=dispatched,
        retrieved={s["source_id"] for s in sources if s["status"] == "checked"},
        qualifying=qualifying,
    )
    verified = next((frame["seconds"] for frame in frames if frame["snapshot"]["recommendations"]), None)
    # Identity-matched provisional displays and retailer checks are separately adjudicated.
    provisional = case.get("first_matching_provisional_seconds", {}).get(row.get("run_id"))
    first = min((s for s in (provisional, verified) if s is not None), default=None)
    classifications = case.get("classification")
    active = worker.get("latency_ms")
    cost = {
        status: sum((Decimal(a["values"][3]) for a in receipts if a["status"] in states), Decimal(0))
        for status, states in [
            ("known", {"completed"}),
            ("uncertain", {"uncertain"}),
            ("held", {"held", "sent"}),
        ]
    }
    questions = {
        frame["snapshot"]["clarification"]["clarification_id"]
        for frame in frames
        if frame["snapshot"].get("clarification")
    }
    binding = row.get("deployment") == worker.get("versions", {}).get("evaluation") if worker else False
    verdicts = case.get("offer_verdicts", {})
    ids = [group["primary"]["offer"]["offer_id"] for group in groups]
    valid = [verdicts.get(row.get("run_id", "") + "/" + key) for key in ids]
    explanation = (final.get("explanation") or {}).get("text", "").lower()
    failure_reasons = [s.get("reason", "") for s in sources if s["status"] == "failed"]
    # Buyer-visible SKU, stock, fulfillment or fit gaps qualify. A count of zero alone does not.
    blocking_facts = [
        r
        for r in failure_reasons
        if any(
            t in r.lower()
            for t in (
                "matching sku",
                "stock evidence",
                "no verified",
                "missing price",
                "payable total",
                "not available at",
                "unknown condition",
            )
        )
    ]
    if "no checked evidence" in explanation or "was not checked" in explanation:
        blocking_facts.append("Missing evidence for a stated buyer priority or software requirement")
    success = None
    if observed and classifications in {"answerable", "no_match_observed"}:
        success = bool(
            active is not None
            and active <= 120000
            and row["outcome"] in {"abstained", "partial", "recommended"}
            and (
                (classifications == "answerable" and valid and all(v == "valid" for v in valid))
                or (classifications == "no_match_observed" and not ids and blocking_facts)
            )
            and row.get("run_id") not in case.get("constraint_violations", [])
        )
    return dict(
        case_id=case_id,
        side=row["side"],
        rep=row["repetition"],
        outcome=row["outcome"],
        bound=binding,
        run=row.get("run_id"),
        contract=row.get("contract_audit", {}),
        classification=classifications,
        success=success,
        ineligible_for_success=row["outcome"] in {"error", "timeout", "cancelled", "clarification_unresolved"}
        or (active is not None and active > 120000),
        constraint_violation=row.get("run_id") in case.get("constraint_violations", []),
        observed_only_success=(
            bool(
                observed
                and active is not None
                and active <= 120000
                and not ids
                and row["outcome"] in {"abstained", "partial"}
                and blocking_facts
                and row.get("run_id") not in case.get("constraint_violations", [])
            )
            if case.get("observed_methods_classification") == "no_match_observed"
            else success
        ),
        active_seconds=active / 1000 if active is not None else None,
        first_seconds=first,
        verified_seconds=verified,
        retained=len(ids),
        verified_alternatives=sum(len(g["additional_sellers"]) for g in groups),
        multi_seller_groups=sum(bool(g["additional_sellers"]) for g in groups),
        verdicts=valid,
        abstained=not bool(groups) if observed else None,
        stop=final.get("stop_reason"),
        clarification_count=len(questions),
        cost=cost,
        stages=stages,
        known_discovery_sources=known,
        unresolved_domain_leads={
            s for s in candidates if s.startswith("lead:") and s[5:].removeprefix("www.") not in origins
        },
        blocking_facts=blocking_facts,
        application_counts=final.get("coverage"),
        source_failures=Counter(s["reason"] for s in sources if s["status"] == "failed" and s.get("reason")),
        plan=worker.get("search_plan") or {},
        rechecked=worker.get("verify", {}).get("rechecked"),
        catalog_offers=next(
            (p["catalog_offers"] for p in worker.get("phases", []) if "catalog_offers" in p), None
        ),
        channel_status=Counter(
            (a["channel"], a["status"])
            for group in worker.get("discovery_channels", [])
            for a in group["attempts"]
        ),
        model_differences=[
            c.get("usage", {}).get("actual_model")
            for c in worker.get("model_calls", [])
            if c.get("usage", {}).get("actual_model") != "openai/gpt-6.1-sol"
        ],
    )


def summarize():
    with gzip.open(ROOT / "trials.jsonl.gz", "rt", encoding="utf8") as stream:
        raw = [json.loads(line) for line in stream]
    decisions = (
        json.loads((ROOT / "adjudications.json").read_bytes())
        if (ROOT / "adjudications.json").exists()
        else {}
    )
    records = [measure(row, decisions) for row in raw]
    if len({(r["case_id"], r["side"], r["rep"]) for r in records}) != len(records):
        raise ValueError("Duplicate original slot")
    report = {}
    for side in ("full-warm", "single-agent-warm", "fixed-heuristic-warm", "full-cold"):
        rows = [r for r in records if r["side"] == side]
        primary = [r for r in rows if r["rep"] == 1]
        answerable = [r for r in rows if r["classification"] == "answerable"]
        stages = {
            stage: dict(
                per_run=distribution([len(r["stages"][stage]) for r in rows]),
                distinct_ids=sorted(set().union(*(r["stages"][stage] for r in rows))),
            )
            for stage in STAGES
        }
        charges = {
            key: sum((r["cost"][key] for r in rows), Decimal(0)) for key in ("known", "uncertain", "held")
        }
        success_n = sum(r["success"] is True for r in rows)
        success_assessed = sum(r["success"] is not None for r in rows)
        verdicts = [v for r in primary for v in r["verdicts"]]
        channel_counts = sum((r["channel_status"] for r in rows), Counter())
        report[side] = dict(
            runs=len(rows),
            cases=len({r["case_id"] for r in rows}),
            expected=23 if side == "full-cold" else 69,
            verified_worker_bindings=sum(r["bound"] for r in rows),
            outcomes=dict(Counter(r["outcome"] for r in rows)),
            agent_rechecked_selected_offers={
                "valid": verdicts.count("valid"),
                "selected_rep_1": len(verdicts),
                "rate": verdicts.count("valid") / len(verdicts) if verdicts and all(verdicts) else None,
            },
            independent_human_offer_validity=None,
            request_success=by_case(rows, "success") if rows and success_assessed == len(rows) else None,
            request_success_on_assessable_cases=by_case(rows, "success"),
            observable_methods_only_success=by_case(rows, "observed_only_success"),
            all_observed_slots_success_bounds={
                "lower": by_case([dict(r, filled=bool(r["success"])) for r in rows], "filled")["rate"],
                "upper": by_case(
                    [
                        dict(r, filled=r["success"] is not False and not r["ineligible_for_success"])
                        for r in rows
                    ],
                    "filled",
                )["rate"],
            },
            flagged_constraint_mismatch_runs=sum(r["constraint_violation"] for r in rows),
            assessed_runs=success_assessed,
            successful_runs=success_n,
            useful_success=by_case(answerable, "success"),
            abstention=by_case(rows, "abstained"),
            active_seconds=distribution(
                [r["active_seconds"] for r in rows if r["active_seconds"] is not None]
            ),
            within_120_seconds=sum(
                r["active_seconds"] is not None and r["active_seconds"] <= 120 for r in rows
            ),
            first_useful_seconds=censored([r["first_seconds"] for r in rows]),
            verified_seconds_answerable=censored([r["verified_seconds"] for r in answerable]),
            all_run_verified_display_observations=sum(r["verified_seconds"] is not None for r in rows),
            costs_usd={k: str(v) for k, v in charges.items()},
            known_usd_per_success=str(charges["known"] / success_n)
            if success_n and success_assessed == len(rows)
            else None,
            retailer_stages=stages,
            unresolved_domain_leads=sorted(set().union(*(r["unresolved_domain_leads"] for r in rows))),
            application_reported_stages={
                k: distribution([r["application_counts"][k] for r in rows if r["application_counts"]])
                for k in STAGES
            },
            additional_verified_sellers=sum(r["verified_alternatives"] for r in rows),
            multi_seller_configurations=sum(r["multi_seller_groups"] for r in rows),
            runs_with_corpus_candidates=sum(bool(r["catalog_offers"]) for r in rows),
            runs_with_direct_recheck=sum(bool(r["rechecked"]) for r in rows),
            runs_preserving_submitted_fields=sum(
                r["contract"].get("input_matches_first_api_snapshot") is True
                and not r["contract"].get("changed_request_fields_across_polls")
                for r in rows
            ),
            runs_promoting_non_domestic_offers=sum(
                bool(r["contract"].get("non_domestic_main_offers")) for r in rows
            ),
            runs_showing_provisional_candidates=sum(
                bool(r["contract"].get("provisional_identity_review_needed")) for r in rows
            ),
            runs_with_corpus_candidates_and_direct_recheck=sum(
                bool(r["catalog_offers"]) and bool(r["rechecked"]) for r in rows
            ),
            cases_with_corpus_candidates_and_direct_recheck=sorted(
                {r["case_id"] for r in rows if r["catalog_offers"] and r["rechecked"]}
            ),
            clarification_questions=sum(r["clarification_count"] for r in rows),
            search_methods=dict(Counter(r["plan"].get("method", "not_recorded") for r in rows)),
            discovery_outcomes={
                channel + "/" + status: n for (channel, status), n in sorted(channel_counts.items())
            },
            source_failure_reasons=dict(sum((r["source_failures"] for r in rows), Counter())),
            model_version_exceptions=[
                dict(run=r["run"], versions=r["model_differences"]) for r in rows if r["model_differences"]
            ],
        )
    return report


if __name__ == "__main__":
    print(json.dumps(summarize(), indent=2, ensure_ascii=False, allow_nan=False))
