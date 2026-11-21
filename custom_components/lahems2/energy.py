"""Normalize measurements and externally owned schedules for Shadow Mode."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from .const import BATTERIES, ROLES, SETTINGS
from .model import numeric_value

QUARTER = timedelta(minutes=15)


def timestamp(value: object) -> datetime | None:
    """Accept absolute ISO timestamps only."""
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.utcoffset() is not None:
                return parsed.astimezone(UTC)
        except (ValueError, OverflowError):
            pass
    return None


def watts(value: object, unit: str | None) -> float | None:
    factor = {"W": 1, "kW": 1000}.get(unit)
    number = numeric_value(value)
    return numeric_value(number * factor) if factor is not None and number is not None else None


def quarter_hours(rows: object, now: datetime, sign: int) -> tuple[str, list[dict]]:
    """Average external power over fully covered upcoming UTC quarters."""
    if not isinstance(rows, (list, tuple)) or not rows:
        return "No timestamped schedule", []
    intervals = []
    for row in rows:
        if not isinstance(row, Mapping):
            return "Invalid schedule", []
        start = timestamp(row.get("start"))
        end = timestamp(row.get("end"))
        power = numeric_value(row.get("power_w"))
        if start is None or end is None or end <= start or power is None:
            return "Invalid schedule", []
        intervals.append((start, end, power * sign))
    intervals.sort()
    if any(left[1] > right[0] for left, right in zip(intervals, intervals[1:])):
        return "Overlapping schedule intervals", []
    if intervals[-1][1] <= now:
        return "Expired", []

    start = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
    result = []
    index = 0
    for _ in range(192):
        end = start + QUARTER
        while index < len(intervals) and intervals[index][1] <= start:
            index += 1
        covered = weighted = 0.0
        cursor = index
        while cursor < len(intervals):
            left, right, power = intervals[cursor]
            cursor += 1
            if left >= end:
                break
            seconds = (min(end, right) - max(start, left)).total_seconds()
            covered += seconds
            weighted += power * (seconds / 900)
        if covered == 900 and numeric_value(weighted) is not None:
            result.append({"start": start.isoformat(), "end": end.isoformat(), "power_w": round(weighted, 3)})
        start = end
    return ("Available" if result else "No fully covered quarters"), result


def energy_snapshot(readings: dict, profile: dict, schedule_rows: object, now: datetime) -> dict:
    """Report measured flows and constraints without dispatching any asset."""
    settings = profile.get(SETTINGS, {})

    def role(key):
        return readings[ROLES[key].label]

    def measured(reading, sign=1):
        power = watts(reading["reading"], reading["unit"]) if reading["usable"] else None
        return power * sign if power is not None else None

    def percent(reading):
        value = numeric_value(reading["reading"]) if reading["usable"] and reading["unit"] == "%" else None
        return value if value is not None and 0 <= value <= 100 else None

    ev_sign = -1 if settings.get("ev_positive_discharge") else 1
    grid = measured(role("grid_meter"), -1 if settings.get("grid_positive_export") else 1)
    ev = measured(role("ev_power"), ev_sign)
    pv = [
        measured(reading) for label, reading in readings.items()
        if label.startswith(ROLES["pv_power"].label + " ")
    ]
    batteries = []
    for index, battery in enumerate(profile.get(BATTERIES, []), 1):
        prefix = f"Battery {index} · "
        batteries.append({
            "power_w": measured(readings[prefix + "Measured power"], -1 if battery.get("power_sign") == "positive_discharge" else 1),
            "soc_percent": percent(readings[prefix + "State of charge"]),
            "capacity_kwh": battery.get("capacity"),
            "reserve_percent": battery.get("reserve"),
            "charge_limit_w": battery.get("charge_limit"),
            "discharge_limit_w": battery.get("discharge_limit"),
            "controller": battery.get("controller"),
        })

    components = pv + [battery["power_w"] for battery in batteries]
    if role("ev_power")["entity_id"]:
        components.append(ev)
    residual = None
    if grid is not None and all(value is not None for value in components) and all(value >= 0 for value in pv):
        residual = numeric_value(grid + sum(pv) - sum(battery["power_w"] for battery in batteries) - (ev or 0))

    schedule = role("ev_schedule")
    schedule_status, intervals = (
        quarter_hours(schedule_rows, now, ev_sign) if schedule["usable"] else (schedule["status"], [])
    )
    connection = role("ev_connected")
    departure = role("ev_departure")
    deadline = timestamp(departure["reading"]) if departure["usable"] else None
    if connection["usable"] and connection["reading"] == "off":
        schedule_status, intervals = "EV disconnected", []
    return {
        "grid_w": grid,
        "pv_w": numeric_value(sum(pv)) if pv and all(value is not None and value >= 0 for value in pv) else None,
        "ev_w": ev,
        "batteries": batteries,
        "unallocated_load_w": residual,
        "import_limit_w": settings.get("import_limit_w"),
        "export_limit_w": settings.get("export_limit_w"),
        "ev_connected": {"on": True, "off": False}.get(connection["reading"]) if connection["usable"] else None,
        "ev_soc_percent": percent(role("ev_soc")),
        "ev_departure": deadline.isoformat() if deadline and deadline > now else None,
        "ev_required_soc_percent": settings.get("ev_required_soc_percent"),
        "ev_schedule_status": schedule_status,
        "external_ev_schedule": intervals,
    }
