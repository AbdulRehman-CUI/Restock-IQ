from datetime import timedelta


def baseline(history, origin):
    valid = history[(history.date >= origin - timedelta(days=27)) & (history.date <= origin)
                    & history.store_open & history.fully_in_stock].sort_values("date").tail(7)
    count = len(valid)
    return {"expected_units": float(valid.units_sold.mean()) if count else None,
            "observation_count": count,
            "reason_codes": ["baseline_fewer_than_seven_observations"] if 0 < count < 7 else
                            ["insufficient_history"] if not count else []}
