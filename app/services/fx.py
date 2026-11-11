"""Daily reference FX, never executable bank prices or future-rate predictions."""
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from email.utils import parsedate_to_datetime
import hashlib
import json
import logging
import re
import time
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select, update
from app.db.market import FXObservation, FXRefresh
from app.db.models import Dataset, FXRate

PROVIDER = "exchange-rate-api"
ENDPOINT = "https://open.er-api.com/v6/latest/USD"
STANDARD_BASE_URL = "https://v6.exchangerate-api.com/v6"
STANDARD_SOURCE = "https://www.exchangerate-api.com/docs/standard-requests"
OPEN_PROVIDER = "exchange-rate-api-open"
ATTRIBUTION = "Rates By Exchange Rate API"
ATTRIBUTION_URL = "https://www.exchangerate-api.com"

# The Standard API puts the key in the URL. HTTPX logs URLs at INFO and
# HTTPcore can log upstream headers and exceptions at DEBUG.
_private_request = ContextVar("private_fx_request", default=False)
for _logger in ("httpx", "httpcore.connection", "httpcore.http11"):
    logging.getLogger(_logger).addFilter(lambda _: not _private_request.get())


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


def parse_observation(payload, fetched_at, rates_key="rates"):
    try:
        data = json.loads(payload, parse_float=Decimal, parse_constant=lambda _: None)
        if data.get("result") == "error":
            error = data.get("error-type")
            if error in ("invalid-key", "inactive-account"):
                raise ProviderError("FX provider rejected the credentials")
            if error == "quota-reached":
                raise ProviderError("FX provider quota reached", fetched_at + timedelta(hours=1))
        if data.get("result") != "success" or data.get("base_code") != "USD":
            raise ValueError("Expected successful USD base response")
        rates = data[rates_key]
        if type(rates["PKR"]) not in (int, Decimal) or type(rates["USD"]) not in (int, Decimal) or rates["USD"] != 1:
            raise ValueError("Invalid base/target currency")
        rate = Decimal(rates["PKR"])
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
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, OSError, InvalidOperation, RecursionError):
        raise ProviderError("Invalid USD/PKR provider response") from None


def retry_after(value, now, default_seconds):
    try:
        seconds = int(value)
        if seconds < 0:
            raise ValueError()
        return now + timedelta(seconds=seconds)
    except (ValueError, TypeError, OverflowError):
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                raise ValueError()
            return max(now, parsed.astimezone(timezone.utc))
        except (ValueError, TypeError, AttributeError, OverflowError):
            return now + timedelta(seconds=default_seconds)


class ExchangeRateAPI:
    def __init__(self, client=None, sleep=time.sleep, *, settings=None):
        self.client, self.sleep = client, sleep
        self.settings = settings

    def fetch(self, fetched_at):
        keyed = self.settings is not None and self.settings.exchange_rate_api_provider == PROVIDER
        url, source, rates_key = ENDPOINT, ENDPOINT, "rates"
        if keyed:
            key = self.settings.exchange_rate_api_key
            if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
                raise ProviderError("FX API key is missing or malformed")
            base = (self.settings.exchange_rate_api_base_url or STANDARD_BASE_URL).rstrip("/")
            try:
                parts = urlsplit(base)
                valid = parts.scheme == "https" and parts.hostname and parts.username is None and not (
                    parts.query or parts.fragment or re.search(r"[\s?#\\]", base))
                # Accessing port also validates any explicit port in the origin.
                valid = valid and (parts.port is None or parts.port > 0)
            except ValueError:
                valid = False
            if not valid:
                raise ProviderError("FX provider base URL must be an HTTPS URL without credentials, query or fragment")
            url, source, rates_key = f"{base}/{key}/latest/USD", STANDARD_SOURCE, "conversion_rates"
        timeout = httpx.Timeout(8, connect=4)
        client = self.client or httpx.Client(timeout=timeout, trust_env=False)
        token = _private_request.set(keyed)
        try:
            for attempt in range(3):
                try:
                    deadline = time.monotonic() + 16
                    with client.stream("GET", url, timeout=timeout, follow_redirects=False,
                                       headers={"Accept": "application/json", "Accept-Encoding": "identity"}) as response:
                        if response.status_code == 429:
                            raise ProviderError("FX provider rate limited the refresh", retry_after(
                                response.headers.get("Retry-After"), fetched_at, 20 * 60))
                        if response.status_code in (500, 502, 503, 504):
                            if "Retry-After" in response.headers:
                                raise ProviderError("FX provider temporarily unavailable", retry_after(
                                    response.headers["Retry-After"], fetched_at, 60))
                            raise httpx.TransportError("Temporary provider failure")
                        if response.status_code not in (200, 400, 401, 403):
                            raise ProviderError("FX provider rejected the refresh")
                        body = bytearray()
                        for part in response.iter_bytes():
                            body.extend(part)
                            if len(body) > 128 * 1024:
                                raise ProviderError("FX provider response exceeded size limit")
                            if time.monotonic() > deadline:
                                raise httpx.ReadTimeout("Provider response deadline exceeded")
                        try:
                            observation = parse_observation(bytes(body), fetched_at, rates_key)
                        except ProviderError as exc:
                            if response.status_code in (401, 403) and exc.retry_at is None:
                                raise ProviderError("FX provider rejected the credentials") from None
                            raise
                        if response.status_code != 200:
                            raise ProviderError("FX provider rejected the refresh")
                        return replace(observation, source=source)
                except httpx.TransportError:
                    if attempt == 2:
                        raise ProviderError("FX provider timed out or was unavailable") from None
                    self.sleep(0.25 * (attempt + 1))
                except (httpx.RequestError, httpx.InvalidURL):
                    raise ProviderError("Invalid FX provider request or response") from None
        finally:
            try:
                if self.client is None:
                    client.close()
            finally:
                _private_request.reset(token)


def observation_view(row):
    return None if row is None else {key: str(getattr(row, key)) for key in (
        "id", "provider", "observed_at", "fetched_at", "next_update_at", "rate", "source")}


def configuration_issue(settings):
    if settings.exchange_rate_api_provider == PROVIDER and not settings.exchange_rate_api_key:
        return "missing_api_key"
    if settings.exchange_rate_api_provider not in (PROVIDER, OPEN_PROVIDER):
        return "unsupported_provider"
    return None


def demo_fx(session, dataset_id, status):
    original = session.scalar(select(FXRate).join(Dataset, Dataset.id == FXRate.dataset_id).where(
        Dataset.id == str(dataset_id), Dataset.provenance == "synthetic", FXRate.currency == "USD",
        FXRate.date <= Dataset.end_date).order_by(FXRate.date.desc()).limit(1))
    if original is None:
        return status
    return dict(status, status="unavailable", data_status="demo", observation=None, age_seconds=None,
                rate=str(original.pkr_per_unit), last_updated_at=original.date.isoformat(),
                provider=None, source="Synthetic dataset", notice="Sample USD/PKR assumption from the synthetic dataset.")


def fx_status(db, settings, now=None, *, fetched=False):
    now = now or now_utc()
    with db.sessions() as session:
        row = session.scalar(select(FXObservation).where(FXObservation.observed_at <= instant(now))
                             .order_by(FXObservation.observed_at.desc()).limit(1))
        refresh = session.get(FXRefresh, PROVIDER)
        age = max(0, int((now - datetime.fromisoformat(row.observed_at)).total_seconds())) if row else None
        issue = configuration_issue(settings)
        missed_update = row and refresh.last_success_at and row.next_update_at <= refresh.last_success_at
        stale = row is not None and (
            age > settings.fx_stale_days * 86400 or missed_update or refresh.error or (issue and not fetched))
        status = "unavailable" if row is None else "stale" if stale else "fresh"
        data_status = status if status != "fresh" else "live" if fetched else "cached"
        return {"status": status, "data_status": data_status, "base_currency": "USD", "target_currency": "PKR",
                "rate": str(row.rate) if row else None, "last_updated_at": row.observed_at if row else None,
                "provider": row.provider if row else PROVIDER if issue != "unsupported_provider" else None,
                "source": row.source if row else None,
                "refresh_enabled": issue is None,
                "fallback_reason": issue or ("refresh_failed" if refresh.error else "observation_expired" if stale else None),
                "observation": observation_view(row), "age_seconds": age,
                "last_attempt_at": refresh.last_attempt_at, "last_success_at": refresh.last_success_at,
                "next_attempt_at": refresh.next_attempt_at, "refresh_failed": bool(refresh.error), "error": refresh.error,
                "attribution": ATTRIBUTION, "attribution_url": ATTRIBUTION_URL,
                "notice": "Daily indicative reference data, not a bank selling rate. Manual executable rate entry is available."}


def with_demo_fx(db, status, dataset_id):
    if dataset_id is not None and status["observation"] is None:
        with db.sessions() as session:
            return demo_fx(session, dataset_id, status)
    return status


def current_fx(db, settings, dataset_id=None):
    now = now_utc()
    status = fx_status(db, settings, now)
    if status["refresh_enabled"] and status["next_attempt_at"] <= instant(now):
        status = refresh_fx(db, settings, now=now)
    return with_demo_fx(db, status, dataset_id)


def refresh_fx(db, settings, provider=None, now=None):
    now = now or now_utc()
    stamp = instant(now)
    if provider is None and configuration_issue(settings):
        return fx_status(db, settings, now)
    # Compare-and-set lease works across processes, PostgreSQL and independent SQLite engines.
    with db.transaction() as session:
        claimed = session.execute(update(FXRefresh).where(FXRefresh.id == PROVIDER,
            FXRefresh.lease_until <= stamp, FXRefresh.next_attempt_at <= stamp).values(
                lease_until=instant(now + timedelta(seconds=90)), last_attempt_at=stamp)).rowcount
    if not claimed:
        return fx_status(db, settings, now)
    fetched = False
    try:
        observation = (provider or ExchangeRateAPI(settings=settings)).fetch(now)
        observed = instant(observation.observed_at)
        with db.transaction() as session:
            row = session.get(FXRefresh, PROVIDER, with_for_update=True)
            if row.last_attempt_at != stamp:
                return fx_status(db, settings, now)
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
            row.last_success_at, row.error, row.lease_until = stamp, None, ""
            # No faster than daily, even if called manually many times or an old observation is returned.
            row.next_attempt_at = instant(max(now + timedelta(days=1), observation.next_update_at))
        fetched = observed <= stamp
    except ProviderError as exc:
        with db.transaction() as session:
            row = session.get(FXRefresh, PROVIDER, with_for_update=True)
            if row.last_attempt_at == stamp:
                row.error, row.lease_until = str(exc), ""
                row.next_attempt_at = instant(max(now + timedelta(minutes=20), exc.retry_at or now))
    return fx_status(db, settings, now, fetched=fetched)
