"""Exercise dispatch constraints for the read-only battery comparison."""

from datetime import UTC, datetime

import pytest

from custom_components.lahems2.energy import QUARTER
from custom_components.lahems2.shadow_plan import Battery, QuarterInput, compare, forecast

START = datetime(2026, 11, 23, 0, 0, tzinfo=UTC)


def test_batteries_do_not_charge_from_ev_export_and_preserve_starting_charge():
    slots = [
        QuarterInput(START, 300, 0, 0, 0.04, 0.03),
        QuarterInput(START + QUARTER, 300, 0, -4000, 0.55, 0.50),
        QuarterInput(START + 2 * QUARTER, 2800, 0, 0, 0.65, 0.45),
        QuarterInput(START + 3 * QUARTER, 300, 0, 0, 0.05, 0.04),
    ]
    battery = Battery(capacity_kwh=4, start_kwh=2, reserve_kwh=1, charge_limit_w=1800, discharge_limit_w=2000)
    plan = compare(slots, [battery, battery], "EUR", 0.9)
    rows = plan["intervals"]
    assert plan["status"] == "Available"
    assert plan["proposed_cost"] < plan["baseline_cost"]
    assert rows[1]["ev_power_w"] == -4000
    assert all(power <= 0 for power in rows[1]["battery_power_w"])
    assert rows[-1]["battery_end_soc_percent"] == [50, 50]
    for row in rows:
        assert all(-2000 <= power <= 1800 for power in row["battery_power_w"])
        assert all(25 <= soc <= 100 for soc in row["battery_end_soc_percent"])
        assert not min(row["battery_power_w"]) < 0 < max(row["battery_power_w"])
    ac_energy = sum(sum(row["battery_power_w"]) / 4000 for row in rows)
    assert ac_energy > 0  # Conversion losses consume energy over the complete cycle.


def test_infeasible_grid_limit_keeps_reference_cost_only():
    slots = [QuarterInput(START, 3500, 0, 0, 0.30, 0.20)]
    battery = Battery(capacity_kwh=4, start_kwh=2, reserve_kwh=1, charge_limit_w=1800, discharge_limit_w=2000)
    plan = compare(slots, [battery], "EUR", import_limit=1000)
    assert plan["status"] == "No feasible proposal found"
    assert plan["baseline_cost"] == pytest.approx(0.2625)
    assert plan["proposed_cost"] is None
    assert plan["intervals"] == []


def test_forecast_keeps_negative_prices_and_rejects_ambiguous_or_unaligned_rows():
    row = {"start": START.isoformat(), "end": (START + QUARTER).isoformat(), "price_per_kwh": -0.12}
    assert forecast([row], "price_per_kwh") == {START: -0.12}
    assert forecast([row, row], "price_per_kwh") == {START: None}
    assert forecast([row], "price_per_kwh", nonnegative=True) == {START: None}
    assert not forecast([{**row, "end": (START + 2 * QUARTER).isoformat()}], "price_per_kwh")
