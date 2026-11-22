"""English setup and options flows for the single heating zone."""

from copy import deepcopy
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
from .sources import Sources

GRID_ROLES = {key: ENERGY_ROLES[key] for key in ("grid_meter", "import_price", "export_price")}
SOLAR_ROLES = {key: ENERGY_ROLES[key] for key in ("pv_power", "solar_forecast")}
EV_ROLES = {key: role for key, role in ENERGY_ROLES.items() if key.startswith("ev_")}
GRID_SETTINGS = (TARIFF_MAX_AGE, "grid_positive_export", "import_limit_w", "export_limit_w")
EV_SETTINGS = ("ev_positive_discharge", "ev_required_soc_percent")


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
        self._section_mode = False
        self._suggestions = {}
        self._sources = None

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

    def _replace_bindings(self, roles, bindings):
        for key in roles:
            self._profile[BINDINGS].pop(key, None)
        self._profile[BINDINGS].update(bindings)

    def _replace_settings(self, keys, values):
        for key in keys:
            self._profile[SETTINGS].pop(key, None)
        self._profile[SETTINGS].update(values)

    def _describe_reading(self, key, role, entity_id):
        reading = self._inputs.reading(key, role, self._inputs.bind(entity_id))
        detail = " ".join(str(part) for part in (reading["reading"], reading["unit"]) if part is not None)
        return f"{entity_id} ({detail or reading['status']})"

    def _suggestion_preview(self, roles):
        return "\n".join(
            f"- **{role.label}** · {escape(self._describe_reading(key, role, self._suggestions[key]))}"
            for key, role in roles.items() if key in self._suggestions
        )

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
                self._replace_bindings(ZONE_ROLES, bindings)
                self._profile[SETTINGS][TEMPERATURE_MAX_AGE] = max_age
                if self._section_mode:
                    return self._finish()
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
                self._replace_bindings(HEAT_ROLES, bindings)
                keys = ("shared_circuit", "pellet_price_threshold", "pellet_budget")
                self._replace_settings(keys, {key: user_input[key] for key in keys if key in user_input})
                if self._section_mode:
                    return self._finish()
                return await self.async_step_energy()
        values = self._saved_values(HEAT_ROLES) | self._previous.get(SETTINGS, {})
        if not self._previous:
            thermostats = self._inputs.candidates(HEAT_ROLES["thermostat"])
            if len(thermostats) == 1:
                values["thermostat"] = thermostats[0].entity_id
        values.update(self._suggestions)
        return self._form(
            "heat", fields, user_input if user_input is not None else values, errors,
            {"suggestions": self._suggestion_preview(HEAT_ROLES)},
        )

    async def async_step_energy(self, user_input=None):
        max_age = self._freshness_default(TARIFF_MAX_AGE)
        limits = {"import_limit_w": None, "export_limit_w": None, "ev_required_soc_percent": 100}
        signs = ("grid_positive_export", "ev_positive_discharge")
        fields = _role_fields(ENERGY_ROLES) | {
            vol.Required(TARIFF_MAX_AGE, default=max_age): _number(1),
            vol.Optional("add_battery", default=False): selector.BooleanSelector(),
        } | {
            vol.Optional(key): _number(0, maximum) for key, maximum in limits.items()
        } | {
            vol.Optional(key, default=False): selector.BooleanSelector() for key in signs
        }
        errors = {}
        if user_input is not None:
            bindings, errors = self._validate_bindings(ENERGY_ROLES, user_input, self._previous.get(BINDINGS, {}))
            max_age = numeric_value(user_input.get(TARIFF_MAX_AGE, max_age))
            if max_age is None or max_age < 1:
                errors[TARIFF_MAX_AGE] = "invalid_number"
            settings = {key: user_input.get(key, False) for key in signs}
            for key, maximum in limits.items():
                if key in user_input:
                    value = numeric_value(user_input[key])
                    if value is None or value < 0 or (maximum is not None and value > maximum):
                        errors[key] = "invalid_number"
                    else:
                        settings[key] = value
            if not errors:
                self._profile[BINDINGS].update(bindings)
                self._profile[SETTINGS].update(settings | {TARIFF_MAX_AGE: max_age})
                if user_input.get("add_battery") or self._saved_batteries:
                    return await self.async_step_battery()
                return await self.async_step_review()
        if self._sources is None:
            self._sources = await Sources.load(self._inputs)
        values = self._inputs.energy_suggestions() | self._sources.defaults()
        previous_settings = self._previous.get(SETTINGS, {})
        values.update({key: previous_settings[key] for key in (*limits, *signs) if key in previous_settings})
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
        }
        if not self._section_mode:
            fields[vol.Optional("add_another", default=False)] = selector.BooleanSelector()
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
                if "battery_power" in bindings:
                    power = self._inputs.entity_id(bindings["battery_power"])
                    ev = self._profile[BINDINGS].get("ev_power")
                    ev_power = self._inputs.entity_id(ev) if ev else None
                    if self._sources.same_storage(power, ev_power, self._profile[SETTINGS].get("ev_energy_source")):
                        errors["battery_power"] = "storage_used_for_ev"
                    elif any(
                        self._sources.same_storage(power, self._inputs.entity_id(battery[BINDINGS]["battery_power"]))
                        for index, battery in enumerate(self._profile[BATTERIES]) if index != self._battery_index
                    ):
                        errors["battery_power"] = "duplicate_battery"
                if not errors:
                    battery = {
                        **previous,
                        BINDINGS: bindings,
                        **{key: user_input[key] for key in minimums},
                        "power_sign": user_input["power_sign"],
                        "controller": user_input["controller"].strip(),
                    }
                    if self._section_mode and existing:
                        self._profile[BATTERIES][self._battery_index] = battery
                    else:
                        self._profile[BATTERIES].append(battery)
            if not errors:
                if self._section_mode:
                    if existing and user_input.get("remove_battery"):
                        self._profile[BATTERIES].pop(self._battery_index)
                    return self._finish()
                self._battery_index += 1
                if self._battery_index < len(self._saved_batteries) or user_input.get("add_another"):
                    return await self.async_step_battery()
                return await self.async_step_review()
        values = self._selection_values(previous.get(BINDINGS, {})) | {
            key: value for key, value in previous.items() if key != BINDINGS
        }
        values.update(self._suggestions)
        return self._form(
            "battery", fields, user_input if user_input is not None else values,
            errors, {"number": str(self._battery_index + 1), "suggestions": self._suggestion_preview(BATTERY_ROLES)},
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
        self._section_mode = True
        self._profile.update(deepcopy(self._previous))
        self._sources = await Sources.load(self._inputs)
        return self.async_show_menu(
            step_id="init",
            menu_options=["energy_import", "area", "heating_source", "grid", "solar", "ev_source", "batteries"],
        )

    def _choice(self, options, *, multiple=False):
        return selector.SelectSelector({
            "options": [{"value": value, "label": label} for value, label in options.items()],
            "multiple": multiple,
            "mode": "dropdown",
        })

    def _source_values(self, sources, input_key, user_input):
        """Validate optional source and device choices on the server."""
        errors = {}
        if (source := user_input.get(input_key)) and source not in sources:
            errors[input_key] = "invalid_source"
        if (device := user_input.get("device")) and not self._inputs.devices.async_get(device):
            errors["device"] = "invalid_source"
        return errors

    async def async_step_energy_import(self, user_input=None):
        defaults = self._sources.defaults()
        sections = {
            "grid": ("Grid & tariffs", GRID_ROLES),
            "solar": ("Solar", SOLAR_ROLES),
        }
        previous = self._previous.get(BINDINGS, {})
        proposals = {}
        lines = []
        for section, (title, roles) in sections.items():
            changed = {}
            current = self._saved_values(roles)
            for key, value in defaults.items():
                if key not in roles:
                    continue
                old = current.get(key)
                grid_sign_changed = key == "grid_meter" and self._previous.get(SETTINGS, {}).get("grid_positive_export")
                if old == value and not grid_sign_changed:
                    continue
                changed[key] = value
                old_label = ", ".join(old) if isinstance(old, list) else old or "Not configured"
                ids = value if isinstance(value, list) else [value]
                readings = [self._describe_reading(key, roles[key], entity_id) for entity_id in ids]
                sign = " · positive means grid import" if key == "grid_meter" else ""
                lines.append(f"- **{roles[key].label}** · {escape(old_label)} → {escape(', '.join(readings))}{sign}")
            if changed:
                proposals[section] = changed
        errors = {}
        if user_input is not None:
            selected = user_input.get("sections", [])
            if not set(selected).issubset(proposals):
                errors["sections"] = "invalid_source"
            elif selected:
                for section in selected:
                    changes = proposals[section]
                    roles = sections[section][1]
                    bindings, invalid = self._validate_bindings(
                        {key: roles[key] for key in changes}, changes, previous
                    )
                    if invalid:
                        errors["sections"] = "invalid_source"
                        break
                    self._profile[BINDINGS].update(bindings)
                    if "grid_meter" in bindings:
                        self._profile[SETTINGS]["grid_positive_export"] = False
                if not errors:
                    return self._finish()
                self._profile = deepcopy(self._previous)
            else:
                return await self.async_step_init()
        fields = {}
        if proposals:
            fields[vol.Optional("sections")] = self._choice(
                {section: sections[section][0] for section in proposals}, multiple=True
            )
        return self._form(
            "energy_import", fields, user_input, errors,
            {"changes": "\n".join(lines) or "No new compatible grid, tariff or solar sources."},
        )

    async def async_step_heating_source(self, user_input=None):
        fields = {vol.Optional("device"): selector.DeviceSelector()}
        meters = {
            source: row.get("name") or source for source, row in self._sources.consumers.items()
            if self._sources.heat_defaults(source)
        }
        if meters:
            fields[vol.Optional("energy_meter")] = self._choice(meters)
        errors = {}
        if user_input is not None:
            errors = self._source_values(meters, "energy_meter", user_input)
            if not errors:
                device = self._sources.device_defaults(
                    user_input.get("device"), ("thermostat", "heat_pump_power", "heat_pump_energy")
                )
                self._suggestions = device | self._sources.heat_defaults(user_input.get("energy_meter"))
                return await self.async_step_heat()
        return self._form("heating_source", fields, user_input, errors)

    def _edit_section(self, step, roles, settings, fields, user_input, suggestions=None):
        errors = {}
        if user_input is not None:
            bindings, errors = self._validate_bindings(roles, user_input, self._previous.get(BINDINGS, {}))
            values = {key: user_input[key] for key in settings if key in user_input}
            number_limits = {
                TARIFF_MAX_AGE: (1, None),
                "import_limit_w": (0, None),
                "export_limit_w": (0, None),
                "ev_required_soc_percent": (0, 100),
            }
            for key, (minimum, maximum) in number_limits.items():
                if key in values:
                    value = numeric_value(values[key])
                    if value is None or value < minimum or (maximum is not None and value > maximum):
                        errors[key] = "invalid_number"
                    else:
                        values[key] = value
            if step == "ev":
                ev_binding = bindings.get("ev_power")
                ev_power = self._inputs.entity_id(ev_binding) if ev_binding else None
                if any(
                    self._sources.same_storage(
                        self._inputs.entity_id(battery[BINDINGS]["battery_power"]), ev_power, self._ev_storage,
                    )
                    for battery in self._saved_batteries
                ):
                    errors["ev_power"] = "storage_used_for_battery"
                if self._ev_storage:
                    values["ev_energy_source"] = self._ev_storage
            if not errors:
                self._replace_bindings(roles, bindings)
                self._replace_settings(settings, values)
                return self._finish()
        defaults = self._saved_values(roles) | {
            key: self._previous[SETTINGS][key] for key in settings
            if key in self._previous.get(SETTINGS, {})
        }
        defaults.update(suggestions or {})
        return self._form(
            step, _role_fields(roles) | fields,
            user_input if user_input is not None else defaults, errors,
            {"suggestions": self._suggestion_preview(roles)} if suggestions else {"suggestions": ""},
        )

    async def async_step_grid(self, user_input=None):
        fields = {
            vol.Required(TARIFF_MAX_AGE, default=self._freshness_default(TARIFF_MAX_AGE)): _number(1),
            vol.Optional("grid_positive_export", default=False): selector.BooleanSelector(),
            vol.Optional("import_limit_w"): _number(0),
            vol.Optional("export_limit_w"): _number(0),
        }
        return self._edit_section("grid", GRID_ROLES, GRID_SETTINGS, fields, user_input)

    async def async_step_solar(self, user_input=None):
        return self._edit_section("solar", SOLAR_ROLES, (), {}, user_input)

    async def async_step_ev_source(self, user_input=None):
        storage = {key: self._sources.storage_label(key, row) for key, row in self._sources.storage().items()}
        fields = {
            vol.Optional("use_v2g", default=False): selector.BooleanSelector(),
            vol.Optional("device"): selector.DeviceSelector(),
        }
        if storage:
            fields[vol.Optional("energy_storage")] = self._choice({"none": "None"} | storage)
        errors = {}
        existing = self._previous.get(SETTINGS, {}).get("ev_energy_source")
        if user_input is not None:
            errors = self._source_values({"none": "None"} | storage, "energy_storage", user_input)
            if not errors:
                picked = user_input.get("energy_storage")
                self._ev_storage = existing if picked is None else storage.get(picked) and picked
                self._suggestions = self._sources.ev_defaults(
                    user_input.get("use_v2g"), picked, user_input.get("device")
                )
                return await self.async_step_ev()
        return self._form(
            "ev_source", fields, user_input if user_input is not None else
            ({"energy_storage": existing} if existing in storage else {}), errors,
        )

    async def async_step_ev(self, user_input=None):
        return self._edit_section("ev", EV_ROLES, (*EV_SETTINGS, "ev_energy_source"), {
            vol.Optional("ev_positive_discharge", default=False): selector.BooleanSelector(),
            vol.Optional("ev_required_soc_percent"): _number(0, 100),
        }, user_input, self._suggestions)

    async def async_step_batteries(self, user_input=None):
        options = {"new": "Add a battery"}
        for index, battery in enumerate(self._saved_batteries):
            power = self._inputs.entity_id(battery[BINDINGS]["battery_power"])
            candidate = self._inputs.candidate(power)
            options[str(index)] = f"Battery {index + 1} · {candidate.name if candidate else power}"
        errors = {}
        if user_input is not None:
            selected = user_input.get("battery_slot")
            if selected in options:
                self._battery_index = len(self._saved_batteries) if selected == "new" else int(selected)
                return await self.async_step_battery_source() if selected == "new" else await self.async_step_battery()
            errors["battery_slot"] = "invalid_source"
        return self._form(
            "batteries", {vol.Required("battery_slot", default="new"): self._choice(options)}, user_input, errors
        )

    async def async_step_battery_source(self, user_input=None):
        ev = self._previous.get(BINDINGS, {}).get("ev_power")
        ev_power = self._inputs.entity_id(ev) if ev else None
        ev_source = self._previous.get(SETTINGS, {}).get("ev_energy_source")
        storage = {
            key: self._sources.storage_label(key, row)
            for key, row in self._sources.storage().items()
            if key != ev_source and key != self._sources.storage_identity(ev_power)
        }
        fields = {vol.Optional("device"): selector.DeviceSelector()}
        if storage:
            fields[vol.Optional("energy_storage")] = self._choice(storage)
        errors = {}
        if user_input is not None:
            errors = self._source_values(storage, "energy_storage", user_input)
            if not errors:
                self._suggestions = self._sources.battery_defaults(
                    user_input.get("energy_storage"), user_input.get("device")
                )
                return await self.async_step_battery()
        return self._form("battery_source", fields, user_input, errors)

    def _finish(self):
        return self.async_create_entry(title="", data=self._profile)
