"""Approximate static FX rates (USD base) for the bundled seed snapshot.
Live mode converts server-side via the Billing Catalog API instead."""
from __future__ import annotations

APPROX_USD_RATES: dict[str, float] = {
    "USD": 1.0,
    "EUR": 0.92,
    "GBP": 0.79,
    "AUD": 1.52,
    "CAD": 1.37,
    "SGD": 1.35,
    "JPY": 157.0,
    "INR": 83.5,
    "VND": 25400.0,
}

SUPPORTED_CURRENCIES = sorted(APPROX_USD_RATES)


def usd_rate(currency: str) -> float:
    return APPROX_USD_RATES[currency.upper()]
