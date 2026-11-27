import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .market import FixtureMarket, RetailMarket
from .router import Router
from .run import search


def main():
    parser = argparse.ArgumentParser(description="Compare new iPhone 17 Pro item prices with OpenRouter.")
    parser.add_argument("query")
    parser.add_argument("--market", choices=["synthetic", "live"], default="synthetic")
    parser.add_argument("--strategy", choices=["fixed", "adaptive"], default="adaptive")
    parser.add_argument("--model", default="openai/gpt-4.1-mini", help="OpenRouter model ID")
    parser.add_argument("--out", type=Path, default=Path("var/scratch-search"))
    args = parser.parse_args()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8]
    out = args.out / run_id
    try:
        router = Router(args.model)
        market = FixtureMarket() if args.market == "synthetic" else RetailMarket(run_id)
        result = asyncio.run(search(args.query, market, router, out, args.strategy))
    except (ValueError, ImportError) as exc:
        parser.exit(2, f"{exc}\n")
    print(result.get("answer") or result.get("error"))
    print(f"{result['status']} | {len(router.calls)} model calls | USD {result['cost_usd']} | "
          f"{result['elapsed_seconds']}s | {out / 'report.json'}")
    return int(result["status"] == "failed")


if __name__ == "__main__":
    raise SystemExit(main())
