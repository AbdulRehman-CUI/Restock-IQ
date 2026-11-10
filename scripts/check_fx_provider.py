"""Optional provider smoke test; never changes an application database."""
import argparse
import json
from dataclasses import asdict
from app.services.fx import ExchangeRateAPI, now_utc
from app.services.recommendations import wire

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly request external daily FX data")
    args = parser.parse_args()
    if not args.live:
        parser.error("Use --live to explicitly permit the external request")
    print(json.dumps(wire(asdict(ExchangeRateAPI().fetch(now_utc()))), indent=2))
