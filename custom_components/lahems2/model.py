"""Input validation and selection independent of Home Assistant."""

from math import isfinite


def numeric_value(value: object) -> float | None:
    """Return a finite number if the reading can be used."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


def active_temperature(primary: dict, backup: dict) -> dict | None:
    """Use an available primary, then an explicitly configured usable backup."""
    for reading in (primary, backup):
        if reading["usable"]:
            return reading
    return None
