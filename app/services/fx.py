"""Daily reference FX, never executable bank prices or future-rate predictions."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from email.utils import parsedate_to_datetime
import hashlib
import json
import time
from typing import Protocol

import httpx
from sqlalchemy import select, update
from app.db.market import FXObservation, FXRefresh

PROVIDER = "exchange-rate-api"
ENDPOINT = "https://open.er-api.com/v6/latest/USD"
ATTRIBUTION = "Rates By Exchange Rate API"
ATTRIBUTION_URL = "https://www.exchangerate-api.com"


def instant(value):
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def now_utc():
    return datetime.now(timezone.utc)


class ProviderError(Exception):
    def __init__(self, message, retry_at=None):
        super().__init__(message)
        self.retry_at = retry_at


@dataclass(frozen=True)
class Observation:
    rate: Decimal
    observed_at: datetime
    next_update_at: datetime
    source: str = ENDPOINT
    provider: str = PROVIDER


class FXProvider(Protocol):
    def fetch(self, fetched_at: datetime) -> Observation: ...


def parse_observation(payload, fetched_at):
    try:
        data = json.loads(payload, parse_float=Decimal, parse_constant=lambda _: None)
        if data.get("result") != "success" or data.get("base_code") != "USD":
            raise ValueError("Expected successful USD base response")
        rates = data["rates"]
        if isinstance(rates["PKR"], bool) or Decimal(str(rates["USD"])) != 1:
            raise ValueError("Invalid base/target currency")
        rate = Decimal(str(rates["PKR"]))
        if not rate.is_finite() or not Decimal("0") < rate <= Decimal("1000000"):
            raise ValueError("Rate must be positive, finite and within supported precision")
        # Provider numbers enter Decimal directly; stored reference precision is 6 dp.
        rate = rate.quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)
        if not rate:
            raise ValueError("Rate rounds to zero")
        timestamps = [data["time_last_update_unix"], data["time_next_update_unix"]]
        if any(type(value) is not int or value <= 0 for value in timestamps):
            raise ValueError("Expected positive integer Unix timestamps")
        observed, upcoming = [datetime.fromtimestamp(value, timezone.utc) for value in timestamps]
        if observed > fetched_at + timedelta(minutes=5) or not observed < upcoming <= observed + timedelta(days=3):
            raise ValueError("Implausible provider timestamp ordering")
        for key, expected in (("time_last_update_utc", observed), ("time_next_update_utc", upcoming)):
            if key in data:
                parsed = parsedate_to_datetime(data[key])
                if parsed.tzinfo is None or parsed.astimezone(timezone.utc) != expected:
                    raise ValueError("Provider timestamps disagree or omit timezone")
        return Observation(rate, observed, upcoming)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, OSError, InvalidOperation) as exc:
        raise ProviderError("Invalid USD/PKR provider response; previous observation retained") from exc


def retry_after(value, now, default_seconds):
    try:
        seconds = int(value)
        if seconds < 0:
            raise ValueError()
        return now + timedelta(seconds=seconds)
    except (ValueError, TypeError, OverflowError):
        try:
            return max(now, parsedate_to_datetime(value).astimezone(timezone.utc))
        except (ValueError, TypeError, AttributeError, OverflowError):
            return now + timedelta(seconds=default_seconds)


class ExchangeRateAPI:
    def __init__(self, client=None, sleep=time.sleep):
        self.client, self.sleep = client, sleep

    def fetch(self, fetched_at):
        client = self.client or httpx.Client(timeout=httpx.Timeout(8, connect=4), follow_redirects=False)
        try:
            for attempt in range(3):
                try:
                    with client.stream("GET", ENDPOINT, timeout=httpx.Timeout(8, connect=4)) as response:
                        if response.status_code == 429:
                            raise ProviderError("FX provider rate limited the refresh", retry_after(
                                response.headers.get("Retry-After"), fetched_at, 20 * 60))
                        if response.status_code in (500, 502, 503, 504):
                            if "Retry-After" in response.headers:
                                raise ProviderError("FX provider temporarily unavailable", retry_after(
                                    response.headers["Retry-After"], fetched_at, 60))
                            raise httpx.TransportError("Temporary provider failure")
                        if response.status_code != 200:
                            raise ProviderError("FX provider rejected the refresh")
                        body = bytearray()
                        for part in response.iter_bytes():
                            body.extend(part)
                            if len(body) > 128 * 1024:
                                raise ProviderError("FX provider response exceeded size limit")
                        return parse_observation(bytes(body), fetched_at)
                except httpx.TransportError as exc:
                    if attempt == 2:
                        raise ProviderError("FX provider timed out or was unavailable; retry later") from exc
                    self.sleep(0.25 * (attempt + 1))
        finally:
            if self.client is None:
                client.close()


def observation_view(row):
    return None if row is None else {key: str(getattr(row, key)) for key in (
        "id", "provider", "observed_at", "fetched_at", "next_update_at", "rate", "source")}


def fx_status(db, settings, now=None):
    now = now or now_utc()
    with db.sessions() as session:
        row = session.scalar(select(FXObservation).where(FXObservation.observed_at <= instant(now))
                             .order_by(FXObservation.observed_at.desc()).limit(1))
        refresh = session.get(FXRefresh, PROVIDER)
        age = max(0, int((now - datetime.fromisoformat(row.observed_at)).total_seconds())) if row else None
        return {"status": "unavailable" if row is None else "stale" if age > settings.fx_stale_days * 86400 else "fresh",
                "observation": observation_view(row), "age_seconds": age,
                "last_attempt_at": refresh.last_attempt_at, "last_success_at": refresh.last_success_at,
                "next_attempt_at": refresh.next_attempt_at, "refresh_failed": bool(refresh.error), "error": refresh.error,
                "attribution": ATTRIBUTION, "attribution_url": ATTRIBUTION_URL,
                "notice": "Daily indicative reference data, not a bank selling rate. Manual executable rate entry is available."}


def refresh_fx(db, settings, provider=None, now=None):
    now = now or now_utc()
    stamp = instant(now)
    # Compare-and-set lease works across processes, PostgreSQL and independent SQLite engines.
    with db.transaction() as session:
        claimed = session.execute(update(FXRefresh).where(FXRefresh.id == PROVIDER,
            FXRefresh.lease_until <= stamp, FXRefresh.next_attempt_at <= stamp).values(
                lease_until=instant(now + timedelta(seconds=90)), last_attempt_at=stamp)).rowcount
    if not claimed:
        return fx_status(db, settings, now)
    try:
        observation = (provider or ExchangeRateAPI()).fetch(now)
        observed = instant(observation.observed_at)
        with db.transaction() as session:
            existing = session.scalar(select(FXObservation).where(FXObservation.provider == observation.provider,
                                                                  FXObservation.observed_at == observed))
            latest = session.scalar(select(FXObservation).order_by(FXObservation.observed_at.desc()).limit(1))
            if existing and (existing.rate != observation.rate or existing.next_update_at != instant(observation.next_update_at)):
                raise ProviderError("Provider changed a previously saved observation; refresh rejected")
            if latest and observed < latest.observed_at:
                raise ProviderError("Provider observation moved backwards; previous observation retained")
            if not existing:
                session.add(FXObservation(id=hashlib.sha256(f"{observation.provider}:{observed}".encode()).hexdigest(),
                    provider=observation.provider, observed_at=observed, fetched_at=stamp, rate=observation.rate,
                    next_update_at=instant(observation.next_update_at), source=observation.source))
            row = session.get(FXRefresh, PROVIDER)
            row.last_success_at, row.error, row.lease_until = stamp, None, ""
            # No faster than daily, even if called manually many times or an old observation is returned.
            row.next_attempt_at = instant(max(now + timedelta(days=1), observation.next_update_at))
    except ProviderError as exc:
        with db.transaction() as session:
            row = session.get(FXRefresh, PROVIDER)
            row.error, row.lease_until = str(exc), ""
            row.next_attempt_at = instant(max(now + timedelta(minutes=20), exc.retry_at or now))
    return fx_status(db, settings, now)
