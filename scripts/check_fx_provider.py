"""Optional provider smoke test; never changes an application database."""
import argparse
import json
from dataclasses import asdict
from app.core.config import Settings
from app.services.fx import ExchangeRateAPI, ProviderError, configuration_issue, now_utc
from app.services.recommendations import wire

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly request external daily FX data")
    args = parser.parse_args()
    if not args.live:
        parser.error("Use --live to explicitly permit the external request")
    settings = Settings.from_env()
    if issue := configuration_issue(settings):
        parser.error(f"FX configuration: {issue}")
    try:
        observation = ExchangeRateAPI(settings=settings).fetch(now_utc())
    except ProviderError as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps(wire(asdict(observation)), indent=2))
