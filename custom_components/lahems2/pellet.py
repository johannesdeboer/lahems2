"""Interpret the reported pellet stock and describe the stove's planning limits."""

from dataclasses import dataclass

from .model import numeric_value


def stock_kg(reading, capacity):
    """Accept absolute kg or an explicit percentage of a configured hopper size."""
    value = numeric_value(reading["reading"]) if reading["usable"] else None
    if value is None or value < 0:
        return None
    if reading["unit"] == "kg":
        return value
    capacity = numeric_value(capacity)
    if reading["unit"] == "%" and value <= 100 and capacity is not None and capacity > 0:
        return value * capacity / 100
    return None


@dataclass(frozen=True)
class Pellet:
    stock_kg: float
    price_per_kg: float
    useful_heat_kwh_per_kg: float
    max_output_kw: float
    heat_pump_cop: float

    @classmethod
    def from_settings(cls, stock, settings):
        """Use measured stock and explicitly supplied heat conversion parameters."""
        values = tuple(numeric_value(settings.get(key)) for key in (
            "pellet_price_per_kg", "pellet_useful_heat_kwh_per_kg",
            "pellet_max_output_kw", "heat_pump_cop",
        ))
        if stock is None or stock <= 0 or None in values or values[0] < 0 or min(values[1:]) <= 0:
            return None
        return cls(stock, *values)
