"""Exercise dispatch constraints for the read-only battery comparison."""

from datetime import UTC, datetime

import pytest

from custom_components.lahems2.energy import QUARTER
from custom_components.lahems2.pellet import Pellet, stock_kg
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


def test_local_heat_uses_stock_on_most_valuable_quarter_then_returns_to_heat_pump():
    slots = [
        QuarterInput(START, 3500, 0, 0, 0.10, 0.05, heat_pump_w=2500),
        QuarterInput(START + QUARTER, 3500, 0, -500, 0.80, 0.35, heat_pump_w=2500),
        QuarterInput(START + 2 * QUARTER, 3500, 0, 0, 0.60, 0.30, heat_pump_w=2500),
        QuarterInput(START + 3 * QUARTER, 3500, 0, 0, 0.90, 0.35),
    ]
    plan = compare(slots, [], "EUR", pellet=Pellet(0.25, 0.5, 4, 4, 2))
    rows = plan["intervals"]
    assert [row["pellet_used_kg"] for row in rows] == [0, 0.25, 0, 0]
    assert [row["pellet_remaining_kg"] for row in rows] == [0.25, 0, 0, 0]
    assert rows[1]["ev_power_w"] == -500
    assert rows[1]["heat_pump_proposed_w"] == 500
    assert rows[2]["heat_pump_proposed_w"] == 2500
    assert rows[3]["heat_pump_proposed_w"] is None
    assert plan["pellet_cost"] == 0.125
    assert plan["baseline_cost"] - plan["proposed_cost"] == pytest.approx(0.275)


def test_hopper_percent_requires_capacity_and_preserves_confirmed_zero():
    reading = {"usable": True, "reading": "25", "unit": "%"}
    assert stock_kg(reading, 12) == 3
    assert stock_kg(reading, None) is None
    assert stock_kg({**reading, "reading": "0"}, 12) == 0
    assert stock_kg({**reading, "usable": False}, 12) is None
