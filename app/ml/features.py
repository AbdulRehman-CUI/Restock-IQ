"""Shared historical and recursive features. All windows exclude the target day."""
from dataclasses import dataclass, field
from datetime import date, timedelta
import numpy as np
import pandas as pd

FEATURE_VERSION = "demand-v1"
CATEGORICAL = ["sku", "category"]
NUMERIC = ["day_of_week", "month", "lag_1", "lag_7", "lag_14", "mean_7", "mean_28",
           "std_7", "availability_28", "promo_active", "missing_fraction_28",
           "lag_1_missing", "lag_7_missing", "lag_14_missing"]
FEATURES = CATEGORICAL + NUMERIC
REQUIRED_HISTORY = ["lag_1", "lag_7", "lag_14", "mean_7", "mean_28", "std_7"]
ASSUMPTIONS = {
    "proxy": "Censored/closed day: mean of earlier uncensored open sales in prior 28 calendar days; NaN if none.",
    "future_store": "open", "future_promotion": "inactive",
    "future_availability": "Assume uncensored/open (1); this is a feature assumption, not a stock guarantee.",
    "price_response": "Not modeled", "uncertainty": "Point forecasts only; no calibrated intervals.",
}


def prepare_history(sales: pd.DataFrame, products: pd.DataFrame, through: date | None = None):
    # Deliberate allowlist: inventory, price, quotes, deliveries and hidden truth
    # cannot become features even if extra columns exist in caller-owned frames.
    columns = ["date", "sku", "units_sold", "promo_active", "fully_in_stock", "store_open"]
    source = sales.loc[:, columns].copy()
    if through is not None:
        source = source[source.date <= through]
    parts = []
    categories = products.set_index("sku").category.to_dict()
    for sku, group in source.groupby("sku", sort=True):
        group = group.sort_values("date").reset_index(drop=True)
        group["category"] = categories[sku]
        group["observed"] = group.store_open & group.fully_in_stock
        observed_sales = group.units_sold.where(group.observed).astype(float)
        prior_mean = observed_sales.shift(1).rolling(28, min_periods=1).mean()
        group["proxy"] = observed_sales.where(group.observed, prior_mean)
        group["proxy_missing"] = group.proxy.isna().astype(int)
        parts.append(group)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def historical_features(prepared):
    frames = []
    for _, group in prepared.groupby("sku", sort=True):
        g = group.sort_values("date").copy()
        g["day_of_week"] = g.date.map(date.weekday)
        g["month"] = g.date.map(lambda d: d.month)
        prior = g.proxy.shift(1)
        for lag in (1, 7, 14):
            g[f"lag_{lag}"] = g.proxy.shift(lag)
            g[f"lag_{lag}_missing"] = g[f"lag_{lag}"].isna().astype(int)
        g["mean_7"] = prior.rolling(7, min_periods=7).mean()
        g["mean_28"] = prior.rolling(28, min_periods=28).mean()
        g["std_7"] = prior.rolling(7, min_periods=7).std(ddof=0)
        g["availability_28"] = g.observed.astype(float).shift(1).rolling(28, min_periods=1).mean()
        g["missing_fraction_28"] = g.proxy_missing.shift(1).rolling(28, min_periods=1).mean()
        frames.append(g)
    return pd.concat(frames, ignore_index=True)


def eligibility(prepared, cutoff):
    result = {}
    for sku, group in prepared[prepared.date <= cutoff].groupby("sku", sort=True):
        days = (cutoff - group.date.min()).days + 1
        observations = int(group.observed.sum())
        result[sku] = {"calendar_days": days, "uncensored_observations": observations,
                       "eligible": days >= 60 and observations >= 30}
    return result


def training_rows(prepared, cutoff):
    available = prepared[prepared.date <= cutoff]
    info = eligibility(available, cutoff)
    features = historical_features(available)
    usable = features.observed & features.sku.isin([s for s, e in info.items() if e["eligible"]])
    usable &= features[REQUIRED_HISTORY].notna().all(axis=1)
    return features.loc[usable].copy(), info


@dataclass
class HistoryState:
    sku: str
    category: str
    proxy: list[float] = field(default_factory=list)
    available: list[float] = field(default_factory=list)

    @classmethod
    def from_frame(cls, sku, category, history):
        return cls(sku, category, history.proxy.astype(float).tolist(), history.observed.astype(float).tolist())

    def features(self, target: date, promo=False):
        row = {"sku": self.sku, "category": self.category, "day_of_week": target.weekday(),
               "month": target.month, "promo_active": int(promo)}
        for lag in (1, 7, 14):
            value = self.proxy[-lag] if len(self.proxy) >= lag else np.nan
            row[f"lag_{lag}"] = value
            row[f"lag_{lag}_missing"] = int(not np.isfinite(value))
        for window in (7, 28):
            values = self.proxy[-window:]
            row[f"mean_{window}"] = float(np.mean(values)) if len(values) == window else np.nan
        row["std_7"] = float(np.std(self.proxy[-7:], ddof=0)) if len(self.proxy) >= 7 else np.nan
        row["availability_28"] = float(np.mean(self.available[-28:])) if self.available else np.nan
        row["missing_fraction_28"] = float(np.mean(~np.isfinite(self.proxy[-28:]))) if self.proxy else np.nan
        return row

    def append_prediction(self, value):
        self.proxy.append(value)
        self.available.append(1.0)
