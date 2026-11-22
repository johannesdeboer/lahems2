"""Compare stationary battery schedules against an idle-battery baseline."""

from bisect import bisect_left, bisect_right
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from math import sqrt

from .energy import QUARTER, timestamp
from .model import numeric_value

_W_PER_QUARTER_KWH = 4000
_EPSILON = 1e-6


def forecast(rows, field, *, nonnegative=False):
    """Decode explicit timestamped quarters. Leave gaps and duplicate quarters unknown."""
    values = {}
    if not isinstance(rows, (list, tuple)):
        return values
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        start, end = timestamp(row.get("start")), timestamp(row.get("end"))
        if not start or end != start + QUARTER or start.minute % 15 or start.second or start.microsecond:
            continue
        value = numeric_value(row.get(field))
        values[start] = value if start not in values and value is not None and (not nonnegative or value >= 0) else None
    return values


@dataclass(frozen=True)
class QuarterInput:
    start: datetime
    demand_w: float
    pv_w: float
    ev_w: float
    import_price: float
    export_price: float
    heat_pump_w: float | None = None

    @property
    def grid_w(self):
        return self.demand_w - self.pv_w + self.ev_w


@dataclass(frozen=True)
class Battery:
    capacity_kwh: float
    start_kwh: float
    reserve_kwh: float
    charge_limit_w: float
    discharge_limit_w: float

    @classmethod
    def from_readings(cls, values):
        capacity, soc, reserve, charge, discharge = (
            numeric_value(values.get(key)) for key in (
                "capacity_kwh", "soc_percent", "reserve_percent", "charge_limit_w", "discharge_limit_w",
            )
        )
        if None in (capacity, soc, reserve, charge, discharge):
            return None
        if capacity <= 0 or not 0 <= reserve <= soc <= 100 or min(charge, discharge) < 0:
            return None
        return cls(capacity, capacity * soc / 100, capacity * reserve / 100, charge, discharge)


def _cost(net, slot):
    return (max(0, net) * slot.import_price - max(0, -net) * slot.export_price) / _W_PER_QUARTER_KWH


def _limit_excess(net, import_limit, export_limit):
    return max(0, net - import_limit) + max(0, -net - export_limit)


def _battery_plan(battery, slots, committed, efficiency, import_limit, export_limit):
    """Find the least-cost path through discrete energy levels for one battery."""
    step = (battery.capacity_kwh - battery.reserve_kwh) / 24
    levels = sorted({
        battery.start_kwh, battery.capacity_kwh,
        *(battery.reserve_kwh + index * step for index in range(24)),
    })
    initial = levels.index(battery.start_kwh)
    # Precompute allowed battery transitions. Limits are AC power limits.
    transitions = []
    charge_kwh = battery.charge_limit_w * efficiency / _W_PER_QUARTER_KWH
    discharge_kwh = battery.discharge_limit_w / efficiency / _W_PER_QUARTER_KWH
    for current in levels:
        low = bisect_left(levels, current - discharge_kwh - _EPSILON)
        high = bisect_right(levels, current + charge_kwh + _EPSILON)
        destinations = []
        for index in range(low, high):
            delta = levels[index] - current
            power = delta * _W_PER_QUARTER_KWH * (1 / efficiency if delta > 0 else efficiency)
            destinations.append((index, power))
        transitions.append(destinations)

    scores = {initial: (0.0, 0.0, 0.0)}
    history = []
    for slot, other in zip(slots, committed, strict=True):
        next_scores = {}
        back = {}
        ev_safe_charge = max(0, slot.pv_w - slot.demand_w) - max(0, other)
        for origin, (excess, cost, throughput) in scores.items():
            for destination, power in transitions[origin]:
                if power * other < -_EPSILON:
                    continue  # Never transfer between stationary batteries.
                if slot.ev_w < 0 and power > max(0, ev_safe_charge) + _EPSILON:
                    continue  # Only independently available PV surplus can charge a battery.
                grid = slot.grid_w + other + power
                score = (
                    excess + _limit_excess(grid, import_limit, export_limit),
                    cost + _cost(grid, slot),
                    throughput + abs(power),
                )
                if destination not in next_scores or score < next_scores[destination]:
                    next_scores[destination] = score
                    back[destination] = origin, power
        history.append(back)
        scores = next_scores

    # End where the baseline ends so energy drawn from the initial SoC is not counted as savings.
    target = initial
    path = []
    for back in reversed(history):
        previous, power = back[target]
        path.append((power, levels[target] / battery.capacity_kwh * 100))
        target = previous
    return list(reversed(path))


def _dispatch(slots, batteries, efficiency, import_limit, export_limit):
    committed = [0.0] * len(slots)
    paths = []
    for battery in batteries:
        path = _battery_plan(battery, slots, committed, efficiency, import_limit, export_limit)
        paths.append(path)
        committed = [other + point[0] for other, point in zip(committed, path, strict=True)]
    grid = [slot.grid_w + power for slot, power in zip(slots, committed, strict=True)]
    return paths, grid


def _fuel_choices(slots, grid, pellet):
    """Use limited fuel first where local heat has the highest estimated value."""
    opportunities = []
    for index, (slot, net) in enumerate(zip(slots, grid, strict=True)):
        if not slot.heat_pump_w:
            continue
        displaced = min(slot.heat_pump_w, pellet.max_output_kw * 1000 / pellet.heat_pump_cop)
        kg = displaced * pellet.heat_pump_cop / (_W_PER_QUARTER_KWH * pellet.useful_heat_kwh_per_kg)
        savings = _cost(net, slot) - _cost(net - displaced, slot) - kg * pellet.price_per_kg
        if savings > _EPSILON:
            opportunities.append((savings / kg, index, displaced, kg))

    choices = {}
    remaining = pellet.stock_kg
    for _, index, watts, kg in sorted(opportunities, key=lambda row: (-row[0], row[1])):
        if kg <= remaining:
            choices[index] = watts, kg
            remaining -= kg
    return choices


def compare(slots, batteries, currency, round_trip_efficiency=0.9, import_limit=None, export_limit=None, pellet=None):
    """Compare fixed heat-pump demand with proposed dispatch and optional local pellet heat."""
    import_limit = float("inf") if import_limit is None else import_limit
    export_limit = float("inf") if export_limit is None else export_limit
    efficiency = sqrt(round_trip_efficiency)
    paths, grid = _dispatch(slots, batteries, efficiency, import_limit, export_limit)

    def feasible(grid_power):
        return all(_limit_excess(net, import_limit, export_limit) <= _EPSILON for net in grid_power)

    def grid_cost(grid_power):
        return sum(_cost(net, slot) for net, slot in zip(grid_power, slots, strict=True))

    fuel = {}
    if pellet and (choices := _fuel_choices(slots, grid, pellet)):
        heated = [
            replace(slot, demand_w=slot.demand_w - choices[index][0]) if index in choices else slot
            for index, slot in enumerate(slots)
        ]
        combined_paths, combined_grid = _dispatch(heated, batteries, efficiency, import_limit, export_limit)
        fuel_cost = sum(kg for _, kg in choices.values()) * pellet.price_per_kg
        if feasible(combined_grid) and (
            not feasible(grid) or grid_cost(combined_grid) + fuel_cost < grid_cost(grid) - _EPSILON
        ):
            paths, grid, fuel = combined_paths, combined_grid, choices

    remaining = pellet.stock_kg if pellet else None
    rows = []
    for index, (slot, proposed) in enumerate(zip(slots, grid, strict=True)):
        offset, kg = fuel.get(index, (0, 0))
        if remaining is not None:
            remaining = max(0, remaining - kg)
        rows.append({
            "start": slot.start.isoformat(), "end": (slot.start + QUARTER).isoformat(),
            "ev_power_w": slot.ev_w, "baseline_grid_w": slot.grid_w, "proposed_grid_w": round(proposed, 3),
            "battery_power_w": [round(path[index][0], 3) for path in paths],
            "battery_end_soc_percent": [round(path[index][1], 3) for path in paths],
            "import_price": slot.import_price, "export_price": slot.export_price,
            "baseline_cost": _cost(slot.grid_w, slot), "proposed_cost": _cost(proposed, slot),
            "heat_pump_baseline_w": slot.heat_pump_w,
            "heat_pump_proposed_w": None if slot.heat_pump_w is None else round(slot.heat_pump_w - offset, 3),
            "pellet_used_kg": round(kg, 4),
            "pellet_remaining_kg": round(remaining, 4) if remaining is not None else None,
        })
    valid = feasible(grid)
    pellet_cost = sum(kg for _, kg in fuel.values()) * pellet.price_per_kg if pellet else 0
    return {
        "status": "Available" if valid else "No feasible proposal found",
        "reason": (
            "EV power is fixed. Proposed batteries preserve their starting charge and cannot charge from EV export."
            if valid else "The proposed schedules could not meet the configured grid limits."
        ),
        "currency": currency,
        "round_trip_efficiency_percent": round_trip_efficiency * 100,
        "baseline_cost": round(sum(row["baseline_cost"] for row in rows), 4),
        "proposed_cost": round(grid_cost(grid) + pellet_cost, 4) if valid else None,
        "pellet_cost": round(pellet_cost, 4) if valid and pellet else None,
        "intervals": rows if valid else [],
    }
