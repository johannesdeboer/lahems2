"""Configuration and input roles for LAHEMS2."""

from dataclasses import dataclass

DOMAIN = "lahems2"
MODE = "Shadow Mode"

AREA = "area"
BINDINGS = "bindings"
BATTERIES = "batteries"
SETTINGS = "settings"

TEMPERATURE_MAX_AGE = "temperature_max_age_minutes"
TARIFF_MAX_AGE = "tariff_max_age_minutes"
FRESHNESS_DEFAULTS = {TEMPERATURE_MAX_AGE: 15, TARIFF_MAX_AGE: 15}


@dataclass(frozen=True)
class Role:
    """An entity input and its expected Home Assistant type."""

    label: str
    domain: str | tuple[str, ...] = "sensor"
    device_class: str | tuple[str, ...] | None = None
    required: bool = False
    report_missing: bool = False
    multiple: bool = False
    numeric: bool = True
    freshness_setting: str | None = None


TEMPERATURE = "temperature"
POWER = "power"
ENERGY = "energy"
PRIMARY = "indoor_temperature"
BACKUP = "backup_temperature"
RELAY = "pellet_heat_request"

ZONE_ROLES = {
    PRIMARY: Role("Primary indoor temperature", device_class=TEMPERATURE, required=True, freshness_setting=TEMPERATURE_MAX_AGE),
    BACKUP: Role("Backup indoor temperature", device_class=TEMPERATURE, freshness_setting=TEMPERATURE_MAX_AGE),
    "outdoor_temperature": Role("Outdoor temperature", device_class=TEMPERATURE, freshness_setting=TEMPERATURE_MAX_AGE),
}
HEAT_ROLES = {
    "thermostat": Role("Heat pump thermostat", "climate", required=True, numeric=False),
    "heat_pump_power": Role("Heat pump power", device_class=POWER),
    "heat_pump_energy": Role("Heat pump energy", device_class=ENERGY),
    "backup_heater_meter": Role("Electric backup heater meter", device_class=(POWER, ENERGY)),
    RELAY: Role("Pellet Heat Request", "switch", numeric=False),
    "heat_demand": Role("Heat demand", ("binary_sensor", "sensor", "climate"), numeric=False),
    "stove_room_temperature": Role("Stove room temperature", device_class=TEMPERATURE, freshness_setting=TEMPERATURE_MAX_AGE),
}
ENERGY_ROLES = {
    "grid_meter": Role("Primary grid meter", device_class=(POWER, ENERGY), required=True),
    "import_price": Role("Import price", report_missing=True, freshness_setting=TARIFF_MAX_AGE),
    "export_price": Role("Export price", report_missing=True, freshness_setting=TARIFF_MAX_AGE),
    "pv_power": Role("Measured solar power", device_class=POWER, multiple=True),
    "solar_forecast": Role("Solar forecast", numeric=False, multiple=True),
    "ev_power": Role("Measured EV power", device_class=POWER),
    "ev_connected": Role("EV connected", "binary_sensor", numeric=False),
    "ev_soc": Role("EV state of charge"),
    "ev_departure": Role("EV departure", device_class="timestamp", numeric=False),
    "ev_schedule": Role("Scheduled EV power", ("sensor", "calendar"), numeric=False),
}
BATTERY_ROLES = {
    "battery_power": Role("Measured power", device_class=POWER, required=True),
    "battery_soc": Role("State of charge", required=True),
}
PLAN_ROLES = {
    "demand_forecast": Role("Household demand forecast", numeric=False),
    "heat_pump_demand_forecast": Role("Heat pump electricity forecast", numeric=False),
    "import_tariff_forecast": Role("Import tariff forecast", numeric=False),
    "export_tariff_forecast": Role("Export tariff forecast", numeric=False),
}
PELLET_ROLES = {
    "pellet_room_temperatures": Role("Pellet stove room temperature", device_class=TEMPERATURE, multiple=True, freshness_setting=TEMPERATURE_MAX_AGE),
    "pellet_room_thermostats": Role("Pellet stove room thermostat", "climate", multiple=True, numeric=False),
    "pellet_stock": Role("Pellet stock estimate", ("sensor", "input_number")),
}
PELLET_NUMBERS = {
    "pellet_hopper_capacity_kg": 0.01,
    "pellet_price_per_kg": 0,
    "pellet_useful_heat_kwh_per_kg": 0.01,
    "pellet_max_output_kw": 0.01,
    "heat_pump_cop": 0.01,
}
ROLES = ZONE_ROLES | HEAT_ROLES | ENERGY_ROLES | PLAN_ROLES | PELLET_ROLES
