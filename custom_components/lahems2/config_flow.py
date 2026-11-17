"""English setup and options flows for the single heating zone."""

from html import escape

import probatio as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    AREA,
    BACKUP,
    BATTERIES,
    BATTERY_ROLES,
    BINDINGS,
    DOMAIN,
    ENERGY_ROLES,
    FRESHNESS_DEFAULTS,
    HEAT_ROLES,
    MODE,
    PRIMARY,
    RELAY,
    ROLES,
    SETTINGS,
    TARIFF_MAX_AGE,
    TEMPERATURE_MAX_AGE,
    ZONE_ROLES,
    Role,
)
from .inputs import Inputs, matches_role
from .model import numeric_value


def _entity_picker(role: Role, include: list[str] | None = None) -> selector.EntitySelector:
    domains = role.domain if isinstance(role.domain, str) else list(role.domain)
    classes = role.device_class
    config = {"filter": [{"domain": domains}], "multiple": role.multiple}
    if classes:
        config["filter"][0]["device_class"] = classes if isinstance(classes, str) else list(classes)
    if include is not None:
        config["include_entities"] = include
    return selector.EntitySelector(config)


def _role_fields(roles: dict[str, Role], *, optional: bool = False) -> dict:
    return {
        (vol.Required if role.required and not optional else vol.Optional)(key): _entity_picker(role)
        for key, role in roles.items()
    }


def _number(minimum: float | None = None, maximum: float | None = None) -> selector.NumberSelector:
    config = {"step": "any", "mode": "box"}
    if minimum is not None:
        config["min"] = minimum
    if maximum is not None:
        config["max"] = maximum
    return selector.NumberSelector(config)


class _Wizard:
    """Share the same selections and validation between setup and Configure."""

    def _start(self, previous: dict) -> None:
        self._inputs = Inputs(self.hass)
        self._previous = previous
        self._profile = {BINDINGS: {}, BATTERIES: [], SETTINGS: {}}
        self._temperature_choices = {}
        self._battery_index = 0
        self._saved_batteries = previous.get(BATTERIES, [])

    def _form(self, step_id, fields, values=None, errors=None, placeholders=None):
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(vol.Schema(fields), values or {}),
            errors=errors,
            description_placeholders=placeholders,
        )

    def _freshness_default(self, setting: str) -> float:
        return self._previous.get(SETTINGS, {}).get(setting, FRESHNESS_DEFAULTS[setting])

    def _selection_values(self, bindings: dict) -> dict:
        return {
            key: [self._inputs.entity_id(item) for item in binding]
            if isinstance(binding, list) else self._inputs.entity_id(binding)
            for key, binding in bindings.items()
        }

    def _saved_values(self, roles: dict[str, Role]) -> dict:
        bindings = self._previous.get(BINDINGS, {})
        return self._selection_values({key: bindings[key] for key in roles if key in bindings})

    def _validate_bindings(
        self, roles: dict[str, Role], values: dict, previous: dict
    ) -> tuple[dict, dict]:
        errors = {}
        bindings = {}
        for key, role in roles.items():
            value = values.get(key)
            if not value:
                if role.required:
                    errors[key] = "required_entity"
                continue
            ids = value if role.multiple else [value]
            stored = previous.get(key)
            old_items = (stored or []) if role.multiple else [stored]
            previous_by_id = {self._inputs.entity_id(item): item for item in old_items if item}
            bound = []
            for entity_id in dict.fromkeys(ids):
                prior = previous_by_id.get(entity_id)
                candidate = self._inputs.candidate(entity_id)
                if not prior and (not candidate or candidate.disabled or not matches_role(candidate, role)):
                    errors[key] = "invalid_entity"
                    break
                binding = self._inputs.bind(entity_id, prior)
                if key == RELAY and not prior and not self._inputs.reading(key, role, binding)["usable"]:
                    errors[key] = "unreadable_relay"
                    break
                bound.append(binding)
            if bound and key not in errors:
                bindings[key] = bound if role.multiple else bound[0]
        return bindings, errors

    async def async_step_area(self, user_input=None):
        errors = {}
        previous_area = self._previous.get(AREA)
        if user_input is not None:
            area_id = user_input.get(AREA)
            if area_id and not self._inputs.areas.async_get_area(area_id):
                errors[AREA] = "invalid_area"
            else:
                self._profile[AREA] = area_id
                if area_id:
                    return await self.async_step_area_temperatures()
                return await self.async_step_temperatures()
        saved_area = {AREA: previous_area} if previous_area else {}
        current = user_input if user_input is not None else saved_area
        return self._form("area", {vol.Optional(AREA): selector.AreaSelector()}, current, errors)

    async def async_step_area_temperatures(self, user_input=None):
        area_id = self._profile[AREA]
        candidates = self._inputs.indoor_suggestions(area_id)
        if not candidates and user_input is None:
            return await self.async_step_temperatures()
        entity_ids = [candidate.entity_id for candidate in candidates]
        roles = {key: ROLES[key] for key in (PRIMARY, BACKUP)}
        errors = {}
        if user_input is not None:
            errors = {key: "invalid_entity" for key, value in user_input.items() if value not in entity_ids}
            if not errors:
                self._temperature_choices = user_input
                return await self.async_step_temperatures()
        values = {key: value for key, value in self._saved_values(roles).items() if value in entity_ids}
        if not self._previous and entity_ids:
            values[PRIMARY] = entity_ids[0]
        return self._form(
            "area_temperatures",
            {vol.Optional(key): _entity_picker(role, entity_ids) for key, role in roles.items()},
            user_input if user_input is not None else values,
            errors,
            {
                "area": escape(self._inputs.area_name(area_id)),
                "suggestions": ", ".join(f"`{entity_id}`" for entity_id in entity_ids[:5]),
            },
        )

    async def async_step_temperatures(self, user_input=None):
        max_age = self._freshness_default(TEMPERATURE_MAX_AGE)
        fields = _role_fields(ZONE_ROLES) | {
            vol.Required(TEMPERATURE_MAX_AGE, default=max_age): _number(1),
        }
        errors = {}
        if user_input is not None:
            bindings, errors = self._validate_bindings(ZONE_ROLES, user_input, self._previous.get(BINDINGS, {}))
            if user_input.get(PRIMARY) and user_input.get(PRIMARY) == user_input.get(BACKUP):
                errors[BACKUP] = "duplicate_temperature"
            max_age = numeric_value(user_input.get(TEMPERATURE_MAX_AGE, max_age))
            if max_age is None or max_age < 1:
                errors[TEMPERATURE_MAX_AGE] = "invalid_number"
            if not errors:
                self._profile[BINDINGS].update(bindings)
                self._profile[SETTINGS][TEMPERATURE_MAX_AGE] = max_age
                return await self.async_step_heat()
        values = self._saved_values(ZONE_ROLES) | self._temperature_choices
        return self._form(
            "temperatures", fields,
            user_input if user_input is not None else values, errors,
        )

    async def async_step_heat(self, user_input=None):
        fields = _role_fields(HEAT_ROLES) | {
            vol.Optional("shared_circuit", default=False): selector.BooleanSelector(),
            vol.Optional("pellet_price_threshold"): _number(),
            vol.Optional("pellet_budget"): _number(0),
        }
        errors = {}
        if user_input is not None:
            bindings, errors = self._validate_bindings(
                HEAT_ROLES, user_input, self._previous.get(BINDINGS, {})
            )
            for key in ("pellet_price_threshold", "pellet_budget"):
                if key in user_input and numeric_value(user_input[key]) is None:
                    errors[key] = "invalid_number"
            if not errors:
                self._profile[BINDINGS].update(bindings)
                self._profile[SETTINGS].update({
                    key: value for key, value in user_input.items() if key not in HEAT_ROLES
                })
                return await self.async_step_energy()
        values = self._saved_values(HEAT_ROLES) | self._previous.get(SETTINGS, {})
        if not self._previous:
            thermostats = self._inputs.candidates(HEAT_ROLES["thermostat"])
            if len(thermostats) == 1:
                values["thermostat"] = thermostats[0].entity_id
        return self._form("heat", fields, user_input if user_input is not None else values, errors)

    async def async_step_energy(self, user_input=None):
        max_age = self._freshness_default(TARIFF_MAX_AGE)
        fields = _role_fields(ENERGY_ROLES) | {
            vol.Required(TARIFF_MAX_AGE, default=max_age): _number(1),
            vol.Optional("add_battery", default=False): selector.BooleanSelector(),
        }
        errors = {}
        if user_input is not None:
            bindings, errors = self._validate_bindings(ENERGY_ROLES, user_input, self._previous.get(BINDINGS, {}))
            max_age = numeric_value(user_input.get(TARIFF_MAX_AGE, max_age))
            if max_age is None or max_age < 1:
                errors[TARIFF_MAX_AGE] = "invalid_number"
            if not errors:
                self._profile[BINDINGS].update(bindings)
                self._profile[SETTINGS][TARIFF_MAX_AGE] = max_age
                if user_input.get("add_battery") or self._saved_batteries:
                    return await self.async_step_battery()
                return await self.async_step_review()
        values = self._saved_values(ENERGY_ROLES) if self._previous else self._inputs.energy_suggestions()
        return self._form("energy", fields, user_input if user_input is not None else values, errors)

    async def async_step_battery(self, user_input=None):
        existing = self._battery_index < len(self._saved_batteries)
        previous = self._saved_batteries[self._battery_index] if existing else {}
        fields = _role_fields(BATTERY_ROLES, optional=True) | {
            vol.Optional("capacity"): _number(0.001),
            vol.Optional("charge_limit"): _number(0),
            vol.Optional("discharge_limit"): _number(0),
            vol.Optional("reserve"): _number(0, 100),
            vol.Optional("power_sign"): selector.SelectSelector({
                "options": ["positive_charge", "positive_discharge"], "translation_key": "power_sign",
            }),
            vol.Optional("controller"): selector.TextSelector(),
            vol.Optional("add_another", default=False): selector.BooleanSelector(),
        }
        if existing:
            fields[vol.Optional("remove_battery", default=False)] = selector.BooleanSelector()
        errors = {}
        if user_input is not None:
            if not (existing and user_input.get("remove_battery")):
                bindings, errors = self._validate_bindings(BATTERY_ROLES, user_input, previous.get(BINDINGS, {}))
                minimums = {"capacity": 0.001, "charge_limit": 0, "discharge_limit": 0, "reserve": 0}
                for key, minimum in minimums.items():
                    value = numeric_value(user_input.get(key))
                    if value is None or value < minimum or (key == "reserve" and value > 100):
                        errors[key] = "invalid_number"
                for key in ("power_sign", "controller"):
                    if not user_input.get(key, "").strip():
                        errors[key] = "required_setting"
                if not errors:
                    self._profile[BATTERIES].append({
                        BINDINGS: bindings,
                        **{key: user_input[key] for key in (*minimums, "power_sign", "controller")},
                    })
            if not errors:
                self._battery_index += 1
                if self._battery_index < len(self._saved_batteries) or user_input.get("add_another"):
                    return await self.async_step_battery()
                return await self.async_step_review()
        values = self._selection_values(previous.get(BINDINGS, {})) | {
            key: value for key, value in previous.items() if key != BINDINGS
        }
        return self._form(
            "battery", fields, user_input if user_input is not None else values,
            errors, {"number": str(self._battery_index + 1)},
        )

    async def async_step_review(self, user_input=None):
        if user_input is not None:
            return self._finish()
        rows = []
        readings = self._inputs.snapshot(self._profile)["roles"]
        for label, reading in readings.items():
            entity = reading["entity_id"]
            value = reading["reading"]
            unit = reading["unit"]
            rendered = " · ".join(
                escape(str(part)).replace("`", "&#96;").replace("\n", " ")
                for part in (entity, value, unit, reading["status"]) if part is not None
            )
            rows.append(f"- **{label}** · {rendered}")
        target = readings[HEAT_ROLES["thermostat"].label]
        if target.get("comfort_target") is not None:
            value = escape(str(target["comfort_target"]))
            unit = escape(str(target["comfort_target_unit"]))
            rows.append(f"- **Thermostat comfort target** · {value} {unit}")
        return self._form("review", {}, placeholders={"readings": "\n".join(rows), "mode": MODE})


class Lahems2ConfigFlow(_Wizard, config_entries.ConfigFlow, domain=DOMAIN):
    """Create a single LAHEMS2 configuration entry."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Create a flow to edit the current household profile."""
        return Lahems2OptionsFlow()

    async def async_step_user(self, user_input=None):
        self._async_abort_entries_match()
        self._start({})
        return await self.async_step_area(user_input)

    def _finish(self):
        self._async_abort_entries_match()
        return self.async_create_entry(title="LAHEMS2", data=self._profile)


class Lahems2OptionsFlow(_Wizard, config_entries.OptionsFlowWithReload):
    """Edit saved bindings and reload Data Status."""

    async def async_step_init(self, user_input=None):
        self._start(dict(self.config_entry.options or self.config_entry.data))
        return await self.async_step_area(user_input)

    def _finish(self):
        return self.async_create_entry(title="", data=self._profile)
