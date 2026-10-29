"""Opt-in read-only check of the public Bank of Russia XML interface."""
import os
from datetime import date
from decimal import Decimal

import pytest

from money_radar.core import CbrRates

pytestmark = [pytest.mark.live, pytest.mark.skipif(os.getenv("RUN_LIVE_TESTS") != "1", reason="set RUN_LIVE_TESTS=1")]


def test_cbr_historical_usd_rate_is_positive_and_cached():
    rates = CbrRates(timeout=15)
    day = date(2025, 1, 15)
    value = rates.rate("USD", day)
    assert value.is_finite() and value > Decimal(0)
    assert rates.rate("USD", day) == value
