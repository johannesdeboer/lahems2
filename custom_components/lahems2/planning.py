"""Evaluate current heating options in Shadow Mode."""

from .const import RELAY, ROLES
from .model import numeric_value


def _value(reading: dict) -> float | None:
    return numeric_value(reading["reading"]) if reading["usable"] else None


def _celsius(value: float, unit: str | None) -> float | None:
    if unit == "°C":
        return value
    if unit == "°F":
        return (value - 32) * 5 / 9
    if unit == "K":
        return value - 273.15
    return None


def propose(snapshot: dict, settings: dict, currency: str) -> tuple[str, dict]:
    """Compare current comfort and tariff readings without issuing commands."""
    roles = snapshot["roles"]
    indoor = snapshot["active_indoor_temperature"]
    thermostat = roles[ROLES["thermostat"].label]
    tariff = roles[ROLES["import_price"].label]
    relay = roles[ROLES[RELAY].label]
    pellet = snapshot.get("pellet_stove")
    threshold = numeric_value(settings.get("pellet_price_threshold"))
    indoor_value = _value(indoor) if indoor else None
    target = numeric_value(thermostat.get("comfort_target"))
    indoor_c = _celsius(indoor_value, indoor["unit"]) if indoor_value is not None else None
    target_c = _celsius(target, thermostat.get("comfort_target_unit")) if target is not None else None
    price = _value(tariff)

    tariff_units = {f"{currency}/kWh"}
    if currency == "EUR":
        tariff_units.add("€/kWh")

    if indoor_c is None or target_c is None:
        state = "Waiting for comfort inputs"
        reason = "A usable indoor temperature and thermostat setpoint with recognized units are needed."
    elif indoor_c >= target_c - 1e-6:
        state = "At or above setpoint"
        reason = "The active indoor temperature meets the thermostat setpoint."
    elif thermostat["reading"] not in {"heat", "heat_cool", "auto"}:
        state = "Review thermostat mode"
        reason = "The room is below the setpoint and the thermostat is outside a heating-capable mode."
    elif pellet and not pellet["stock_kg"]:
        state = "Heat pump is the baseline"
        reason = (
            "The pellet stove is out of pellets. Check the stove room temperature."
            if pellet["stock_kg"] == 0 else "Pellet stock is unknown."
        )
    elif pellet and not any(reading["usable"] for reading in pellet["rooms"].values()):
        state = "Heat pump is the baseline"
        reason = "Pellet comparison needs a usable temperature sensor or thermostat from the stove room."
    elif threshold is None or (not pellet and not relay["usable"]):
        state = "Heat pump is the baseline"
        reason = "The room is below the setpoint. Pellet comparison needs a configured tariff threshold and available pellet heat."
    elif price is None or tariff["unit"] not in tariff_units:
        state = "Waiting for import tariff"
        reason = "Pellet comparison needs a usable import tariff per kWh in the threshold's currency."
    elif price <= threshold:
        state = "Heat pump is the baseline"
        reason = "The room is below the setpoint and the import tariff is at or below the pellet review threshold."
    else:
        state = "Review pellet heat"
        reason = "The room is below the setpoint and the import tariff exceeds the pellet review threshold."

    return state, {
        "reason": reason,
        "indoor_source": snapshot["active_indoor_source"],
        "comfort_gap_celsius": round(target_c - indoor_c, 2) if target_c is not None and indoor_c is not None else None,
        "import_price": price,
        "import_price_unit": tariff["unit"],
        "pellet_price_threshold": threshold,
    }
