"""Exercise the setup, Configure and Data Status with Home Assistant Core."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.components import frontend
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from pytest_homeassistant.common import MockConfigEntry

from custom_components.lahems2.const import (
    BACKUP, BATTERIES, BINDINGS, DOMAIN, MODE, PRIMARY, RELAY, SETTINGS, TARIFF_MAX_AGE, TEMPERATURE_MAX_AGE,
)
from custom_components.lahems2.inputs import Inputs

THERMOSTAT = "climate.heat_pump"
GRID = "sensor.grid_power"
RELAY_ENTITY = "switch.pellet_heat_request"
UNASSIGNED = "sensor.unassigned_temperature"


@pytest.fixture
def household(hass):
    """Create synthetic Home Assistant entities and registry entries."""
    areas = ar.async_get(hass)
    rooms = areas.async_create("Living room"), areas.async_create("Bedroom")
    demo = MockConfigEntry(domain="demo")
    demo.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=demo.entry_id, identifiers={("demo", "indoor")}, name="Temperature sensors"
    )
    dr.async_get(hass).async_update_device(device.id, area_id=rooms[0].id)
    registry = er.async_get(hass)
    entities = {}
    for name in ("living_room_air", "battery_temperature", "bedroom_air"):
        entry = registry.async_get_or_create(
            "sensor", "demo", name, suggested_object_id=name,
            original_name=name.replace("_", " ").title(), original_device_class="temperature",
            device_id=device.id, config_entry=demo,
        )
        entities[name] = entry.entity_id
        hass.states.async_set(
            entry.entity_id, "19.5", {"device_class": "temperature", "unit_of_measurement": "°C"}
        )
    registry.async_update_entity(entities["bedroom_air"], area_id=rooms[1].id)

    hass.states.async_set(UNASSIGNED, "20", {"device_class": "temperature", "unit_of_measurement": "°C"})
    hass.states.async_set(THERMOSTAT, "heat", {"temperature": 21, "temperature_unit": "°C"})
    hass.states.async_set(GRID, "240", {"device_class": "power", "unit_of_measurement": "W"})
    hass.states.async_set("sensor.import_price", "0.22", {"unit_of_measurement": "EUR/kWh"})
    hass.states.async_set("sensor.battery_power", "-120", {"device_class": "power"})
    hass.states.async_set("sensor.battery_state_of_charge", "64", {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.ev_power", "800", {"device_class": "power"})

    esphome = MockConfigEntry(domain="esphome")
    esphome.add_to_hass(hass)
    relay = registry.async_get_or_create(
        "switch", "esphome", "heat_request", config_entry=esphome, suggested_object_id="pellet_heat_request"
    )
    assert relay.entity_id == RELAY_ENTITY
    hass.states.async_set(RELAY_ENTITY, "off")
    return rooms, entities


async def submit(manager, result, step, values):
    """Submit a form using the same flow manager as the frontend."""
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == step
    return await manager.async_configure(result["flow_id"], values)


async def choose(manager, entry, section):
    result = await manager.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.MENU
    return await manager.async_configure(result["flow_id"], {"next_step_id": section})


def status(hass, entry):
    """Access the diagnostic sensor through its registered identity."""
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_data_status")
    assert entity_id
    state = hass.states.get(entity_id)
    assert state
    return state.attributes


@pytest.mark.parametrize("with_relay", [False, True])
async def test_setup_and_edit_in_shadow_mode(hass, household, with_relay):
    """Accept both household types and add or remove the ESPHome binding later."""
    (living, bedroom), entities = household
    indoor = entities["living_room_air"]
    backup = entities["bedroom_air"]
    setup = hass.config_entries.flow

    # Fail at the call boundary before a service can run on any device.
    with patch.object(type(hass.services), "async_call", autospec=True) as call_service:
        result = await setup.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await submit(setup, result, "area", {"area": living.id})
        suggestions = result["description_placeholders"]["suggestions"]
        assert backup not in suggestions
        assert suggestions.index(indoor) < suggestions.index(entities["battery_temperature"])
        primary_picker = next(value for key, value in result["data_schema"].schema.items() if key.schema == PRIMARY)
        assert primary_picker.config["include_entities"][0] == indoor

        result = await submit(setup, result, "area_temperatures", {PRIMARY: indoor})
        result = await submit(setup, result, "temperatures", {PRIMARY: indoor, BACKUP: backup})
        if with_relay:
            hass.states.async_set(RELAY_ENTITY, "unavailable")
            result = await submit(setup, result, "heat", {"thermostat": THERMOSTAT, RELAY: RELAY_ENTITY})
            assert result["errors"] == {RELAY: "unreadable_relay"}
            hass.states.async_set(RELAY_ENTITY, "off")
        heat = {"thermostat": THERMOSTAT}
        if with_relay:
            heat[RELAY] = RELAY_ENTITY
        result = await submit(setup, result, "heat", heat)
        if with_relay:
            result = await submit(setup, result, "energy", {"grid_meter": GRID, "ev_power": "sensor.ev_power", "add_battery": True})
            result = await submit(setup, result, "battery", {
                "battery_power": "sensor.battery_power", "battery_soc": "sensor.battery_state_of_charge",
                "capacity": 5, "charge_limit": 1500, "discharge_limit": 1000, "reserve": 20,
                "power_sign": "positive_charge", "controller": "Synthetic battery controller",
            })
        else:
            result = await submit(setup, result, "energy", {"grid_meter": GRID})
        assert indoor in result["description_placeholders"]["readings"]
        assert "Missing source" in result["description_placeholders"]["readings"]
        result = await submit(setup, result, "review", {})
        assert result["type"] == FlowResultType.CREATE_ENTRY
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        await hass.async_block_till_done()
        attrs = status(hass, entry)
        assert attrs["mode"] == MODE
        assert attrs["active_indoor_temperature"]["entity_id"] == indoor
        assert entry.data[SETTINGS][TEMPERATURE_MAX_AGE] == 15
        assert entry.data[SETTINGS][TARIFF_MAX_AGE] == 15
        assert (RELAY in entry.data[BINDINGS]) == with_relay
        if with_relay:
            assert attrs["roles"]["Battery 1 · Measured power"]["reading"] == "-120"
            assert attrs["roles"]["Measured EV power"]["reading"] == "800"

        options = hass.config_entries.options
        result = await choose(options, entry, "area")
        result = await submit(options, result, "area", {"area": bedroom.id})
        assert result["description_placeholders"]["suggestions"] == f"`{backup}`"
        result = await submit(options, result, "area_temperatures", {PRIMARY: backup})
        result = await submit(options, result, "temperatures", {PRIMARY: UNASSIGNED, TEMPERATURE_MAX_AGE: 8})
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert (RELAY in entry.options[BINDINGS]) == with_relay
        result = await choose(options, entry, "heating_source")
        result = await submit(options, result, "heating_source", {})
        heat = {"thermostat": THERMOSTAT}
        if not with_relay:
            heat[RELAY] = RELAY_ENTITY
        result = await submit(options, result, "heat", heat)
        assert result["type"] == FlowResultType.CREATE_ENTRY
        result = await choose(options, entry, "grid")
        result = await submit(options, result, "grid", {
            "grid_meter": GRID, "import_price": "sensor.import_price",
            "export_price": "sensor.import_price", TARIFF_MAX_AGE: 20,
        })
        if with_relay:
            result = await choose(options, entry, "batteries")
            result = await submit(options, result, "batteries", {"battery_slot": "0"})
            result = await submit(options, result, "battery", {"remove_battery": True})
            assert not entry.options[BATTERIES]
        assert result["type"] == FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
        attrs = status(hass, entry)
        assert attrs["mode"] == MODE
        assert attrs["active_indoor_temperature"]["entity_id"] == UNASSIGNED
        assert attrs["roles"]["Pellet Heat Request"]["status"] == ("Not configured" if with_relay else "Available")
        assert attrs["roles"]["Import price"]["reading"] == "0.22"
        assert attrs["roles"]["Export price"]["entity_id"] == "sensor.import_price"
        assert attrs["roles"]["Import price"]["max_age_minutes"] == 20
        assert attrs["active_indoor_temperature"]["max_age_minutes"] == 8
        await hass.config_entries.async_reload(entry.entry_id)
        assert status(hass, entry)["active_indoor_temperature"]["entity_id"] == UNASSIGNED
        call_service.assert_not_called()


async def test_energy_import_keeps_saved_sections_and_reads_live_values(hass, household):
    inputs = Inputs(hass)
    old_grid = "sensor.old_grid_power"
    solar = ["sensor.solar_east", "sensor.solar_west"]
    for entity_id, value in [(old_grid, "12"), (solar[0], "500"), (solar[1], "300")]:
        hass.states.async_set(entity_id, value, {"device_class": "power", "unit_of_measurement": "W"})
    counter = "sensor.grid_energy"
    hass.states.async_set(counter, "123", {"device_class": "energy", "unit_of_measurement": "kWh"})
    entry = MockConfigEntry(domain=DOMAIN, data={
        BINDINGS: {
            PRIMARY: inputs.bind(UNASSIGNED),
            "thermostat": inputs.bind(THERMOSTAT),
            "grid_meter": inputs.bind(old_grid),
        },
        SETTINGS: {"grid_positive_export": True, "import_limit_w": 8000},
        BATTERIES: [],
    })
    entry.add_to_hass(hass)
    prefs = {
        "energy_sources": [
            {"type": "grid", "stat_rate": GRID, "stat_energy_from": counter, "entity_energy_price": "sensor.import_price"},
            {"type": "grid", "stat_rate": GRID, "entity_energy_price_export": "sensor.import_price"},
            {"type": "solar", "stat_rate": solar[0]},
            {"type": "solar", "stat_rate": solar[1]},
            {"type": "solar", "stat_rate": solar[0]},
            {"type": "water", "stat_energy_from": "sensor.water"},
        ],
    }
    with patch("custom_components.lahems2.sources.async_get_manager", return_value=SimpleNamespace(data=prefs)):
        manager = hass.config_entries.options
        stale = await choose(manager, entry, "grid")
        result = await choose(manager, entry, "energy_import")
        assert old_grid in result["description_placeholders"]["changes"]
        assert f"{solar[0]} (500 W)" in result["description_placeholders"]["changes"]
        assert "water" not in result["description_placeholders"]["changes"]
        result = await submit(manager, result, "energy_import", {"sections": ["grid", "solar"]})
        stale = await submit(manager, stale, "grid", {"grid_meter": old_grid})
        assert stale["type"] == FlowResultType.ABORT
        assert stale["reason"] == "settings_changed"
    assert result["type"] == FlowResultType.CREATE_ENTRY
    profile = entry.options
    assert profile[BINDINGS][PRIMARY] == entry.data[BINDINGS][PRIMARY]
    assert profile[BINDINGS]["grid_meter"]["entity_id"] == GRID
    assert [binding["entity_id"] for binding in profile[BINDINGS]["pv_power"]] == solar
    assert profile[SETTINGS] == {"grid_positive_export": False, "import_limit_w": 8000}
    assert inputs.snapshot(profile)["energy"]["pv_w"] == 800
    hass.states.async_set(solar[0], "600", {"device_class": "power", "unit_of_measurement": "W"})
    assert inputs.snapshot(profile)["energy"]["pv_w"] == 900
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_storage_source_can_only_be_used_once(hass, household):
    inputs = Inputs(hass)
    ev_power = "sensor.charger_real_charging_power"
    normalized_power = "sensor.ev_power"
    schedule = "sensor.v2g_liberty_accepted_ev_schedule"
    hass.states.async_set(ev_power, "-800", {"device_class": "power", "unit_of_measurement": "W"})
    hass.states.async_set(schedule, "active", {"intervals": []})
    identity = "sensor.ev_discharge_energy"
    prefs = {"energy_sources": [{
        "type": "battery", "stat_energy_from": identity, "stat_rate": normalized_power,
        "power_config": {"stat_rate_inverted": ev_power},
    }]}
    entry = MockConfigEntry(domain=DOMAIN, data={
        BINDINGS: {"grid_meter": inputs.bind(GRID)},
        SETTINGS: {},
        BATTERIES: [{
            BINDINGS: {"battery_power": inputs.bind(normalized_power), "battery_soc": inputs.bind("sensor.battery_state_of_charge")},
            "capacity": 5, "charge_limit": 1500, "discharge_limit": 1000, "reserve": 20,
            "power_sign": "positive_discharge", "controller": "External controller",
        }],
    })
    entry.add_to_hass(hass)
    manager = hass.config_entries.options
    with patch("custom_components.lahems2.sources.async_get_manager", return_value=SimpleNamespace(data=prefs)):
        result = await choose(manager, entry, "ev_source")
        result = await submit(manager, result, "ev_source", {"use_v2g": True, "energy_storage": identity})
        defaults = {key.schema: key.description.get("suggested_value") for key in result["data_schema"].schema if key.description}
        assert defaults["ev_power"] == ev_power
        assert defaults["ev_schedule"] == schedule
        result = await submit(manager, result, "ev", {"ev_power": ev_power, "ev_schedule": schedule})
        assert result["errors"] == {"ev_power": "storage_used_for_battery"}
        assert not entry.options
        manager.async_abort(result["flow_id"])

        result = await choose(manager, entry, "batteries")
        result = await submit(manager, result, "batteries", {"battery_slot": "0"})
        result = await submit(manager, result, "battery", {"remove_battery": True})
        assert result["type"] == FlowResultType.CREATE_ENTRY

        result = await choose(manager, entry, "ev_source")
        result = await submit(manager, result, "ev_source", {"use_v2g": True, "energy_storage": identity})
        result = await submit(manager, result, "ev", {"ev_power": ev_power, "ev_schedule": schedule})
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert entry.options[SETTINGS]["ev_energy_source"] == identity
        assert inputs.snapshot(entry.options)["energy"]["ev_w"] == -800
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_shadow_plan_reads_live_sources_and_stops_at_missing_quarter(hass, household):
    start = datetime(2026, 11, 23, 14, tzinfo=UTC)
    inputs = Inputs(hass)
    demand, prices = "sensor.house_demand_forecast", "sensor.future_prices"
    for entity_id, field, values, attrs in (
        (demand, "power_w", [2500, 2500, 2500], {}),
        (prices, "price_per_kwh", [0.1, 0.4], {"unit_of_measurement": "EUR/kWh"}),
    ):
        rows = [{
            "start": (start + timedelta(minutes=15 * index)).isoformat(),
            "end": (start + timedelta(minutes=15 * (index + 1))).isoformat(),
            field: value,
        } for index, value in enumerate(values)]
        hass.states.async_set(entity_id, "ready", {**attrs, "intervals": rows})

    entry = MockConfigEntry(domain=DOMAIN, data={
        BINDINGS: {
            PRIMARY: inputs.bind(UNASSIGNED),
            "thermostat": inputs.bind(THERMOSTAT),
            "grid_meter": inputs.bind(GRID),
            "demand_forecast": inputs.bind(demand),
            "import_tariff_forecast": inputs.bind(prices),
            "export_tariff_forecast": inputs.bind(prices),
        },
        SETTINGS: {"planning_efficiency_percent": 90},
        BATTERIES: [{
            BINDINGS: {"battery_power": inputs.bind("sensor.battery_power"), "battery_soc": inputs.bind("sensor.battery_state_of_charge")},
            "capacity": 5, "charge_limit": 2000, "discharge_limit": 2000, "reserve": 5,
            "power_sign": "positive_charge", "controller": "Test controller",
        }],
    })
    entry.add_to_hass(hass)
    hass.config.currency = "EUR"
    with patch("custom_components.lahems2.sensor.utcnow", return_value=start):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_energy_balance")
        plan = hass.states.get(entity_id).attributes["shadow_plan"]
        assert plan["status"] == "Available"
        assert len(plan["intervals"]) == 2
        assert plan["proposed_cost"] < plan["baseline_cost"]
        hass.states.async_set(prices, "unavailable")
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).attributes["shadow_plan"]["status"] == "Waiting for forecasts"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_data_status_and_temperature_fallback(hass, household):
    """Keep usable inputs when other roles go offline, move or disappear."""
    (living, bedroom), entities = household
    indoor = entities["living_room_air"]
    backup = entities["bedroom_air"]
    registry = er.async_get(hass)
    entry = MockConfigEntry(domain=DOMAIN, data={
        BINDINGS: {
            PRIMARY: {"entity_id": indoor, "registry_id": registry.async_get(indoor).id, "area": living.id},
            BACKUP: {"entity_id": backup, "registry_id": registry.async_get(backup).id, "area": bedroom.id},
        },
    })
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set(backup, "unavailable", {"device_class": "temperature"})
    await hass.async_block_till_done()
    attrs = status(hass, entry)
    assert attrs["roles"]["Backup indoor temperature"]["status"] == "Unavailable"
    assert attrs["roles"]["Import price"]["status"] == "Missing source"
    assert attrs["active_indoor_source"] == "Primary"

    registry.async_update_entity(indoor, area_id=bedroom.id)
    await hass.async_block_till_done()
    attrs = status(hass, entry)
    assert attrs["roles"]["Primary indoor temperature"]["status"] == "Moved to a different area"
    assert attrs["active_indoor_source"] == "Primary"

    hass.states.async_set(backup, "18.5", {"device_class": "temperature"})
    hass.states.async_set(indoor, "unavailable", {"device_class": "temperature"})
    await hass.async_block_till_done()
    assert status(hass, entry)["active_indoor_source"] == "Backup"

    registry.async_update_entity(backup, disabled_by=er.RegistryEntryDisabler.USER)
    registry.async_remove(indoor)
    await hass.async_block_till_done()
    attrs = status(hass, entry)
    assert attrs["roles"]["Primary indoor temperature"]["status"] == "Entity removed"
    assert attrs["roles"]["Backup indoor temperature"]["status"] == "Entity disabled"
    assert attrs["active_indoor_source"] is None


async def test_freshness_defaults_and_overrides(hass, household):
    """Apply independent limits to old profiles and honor explicit settings."""
    primary = household[1]["living_room_air"]
    tariff = "sensor.import_price"
    inputs = Inputs(hass)
    profile = {BINDINGS: {PRIMARY: inputs.bind(primary), "import_price": inputs.bind(tariff)}}
    now = max(hass.states.get(entity).last_reported for entity in (primary, tariff))
    with patch("custom_components.lahems2.inputs.utcnow", return_value=now + timedelta(minutes=15)):
        default = inputs.snapshot(profile)
        assert default["roles"]["Import price"]["status"] == "Stale reading"
        assert default["roles"]["Primary indoor temperature"]["status"] == "Stale reading"
        assert default["active_indoor_source"] is None
        profile[SETTINGS] = {TEMPERATURE_MAX_AGE: 30, TARIFF_MAX_AGE: 15}
        adjusted = inputs.snapshot(profile)
        assert adjusted["roles"]["Import price"]["status"] == "Stale reading"
        assert adjusted["active_indoor_source"] == "Primary"


async def test_actual_and_proposed_update_without_device_calls(hass, household):
    """Update the dashboard entities from thermostat, temperature and tariff changes."""
    (living, _), entities = household
    indoor = entities["living_room_air"]
    hass.config.currency = "EUR"
    inputs = Inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, data={
        "area": living.id,
        BINDINGS: {
            PRIMARY: inputs.bind(indoor),
            "thermostat": inputs.bind(THERMOSTAT),
            "import_price": inputs.bind("sensor.import_price"),
            RELAY: inputs.bind(RELAY_ENTITY),
        },
        SETTINGS: {"pellet_price_threshold": 0.30},
    })
    entry.add_to_hass(hass)
    with patch.object(type(hass.services), "async_call", autospec=True) as call_service:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        registry = er.async_get(hass)
        actual_id = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_actual")
        proposed_id = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_proposed")
        assert hass.states.get(actual_id).attributes["indoor_temperature"] == "19.5"
        assert hass.states.get(proposed_id).state == "Heat pump is the baseline"

        hass.states.async_set("sensor.import_price", "0.40", {"unit_of_measurement": "EUR/kWh"})
        await hass.async_block_till_done()
        proposal = hass.states.get(proposed_id)
        assert proposal.state == "Review pellet heat"
        assert proposal.attributes["comfort_gap_celsius"] == 1.5

        hass.states.async_set("sensor.import_price", "40", {"unit_of_measurement": "ct/kWh"})
        await hass.async_block_till_done()
        assert hass.states.get(proposed_id).state == "Waiting for import tariff"

        hass.states.async_set(THERMOSTAT, "off", {"temperature": 21, "temperature_unit": "°C"})
        await hass.async_block_till_done()
        assert hass.states.get(proposed_id).state == "Review thermostat mode"

        hass.states.async_set(indoor, "69.8", {"device_class": "temperature", "unit_of_measurement": "°F"})
        await hass.async_block_till_done()
        assert hass.states.get(proposed_id).state == "At or above setpoint"

        hass.states.async_set(indoor, "unavailable", {"device_class": "temperature"})
        await hass.async_block_till_done()
        assert hass.states.get(actual_id).state == "Indoor temperature unavailable"
        assert hass.states.get(proposed_id).state == "Waiting for comfort inputs"
        assert hass.states.get(RELAY_ENTITY).state == "off"
        call_service.assert_not_called()


async def test_energy_balance_and_external_schedule(hass, household):
    """Expose an externally owned plan and keep EV export separate from PV."""
    from homeassistant.util.dt import utcnow

    now = utcnow()
    quarter = timedelta(minutes=15)
    current_quarter = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
    start = current_quarter + quarter
    end = start + quarter
    hass.states.async_set(GRID, "-1", {"device_class": "power", "unit_of_measurement": "kW"})
    hass.states.async_set("sensor.ev_power", "-1500", {"device_class": "power", "unit_of_measurement": "W"})
    change = start + timedelta(minutes=5)
    hass.states.async_set("sensor.ev_plan", "active", {"intervals": [
        {"start": start.isoformat(), "end": change.isoformat(), "power_w": -3000},
        {"start": change.isoformat(), "end": end.isoformat(), "power_w": 1500},
        {"start": end.isoformat(), "end": (end + quarter).isoformat(), "power_w": -1500},
        {"start": (end + quarter).isoformat(), "end": (end + 2 * quarter).isoformat(), "power_w": 2000},
    ]})
    inputs = Inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, data={
        BINDINGS: {key: inputs.bind(entity) for key, entity in {
            "grid_meter": GRID, "ev_power": "sensor.ev_power", "ev_schedule": "sensor.ev_plan",
        }.items()},
    })
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_energy_balance")
    assert frontend.async_panel_exists(hass, "lahems2-overview")
    attributes = hass.states.get(entity_id).attributes
    assert attributes["lahems2_view"] == "energy_balance"
    assert attributes["grid_w"] == -1000
    assert attributes["ev_w"] == -1500
    assert attributes["pv_w"] is None
    assert attributes["unallocated_load_w"] == 500
    assert attributes["external_ev_schedule"] == [
        {"start": start.isoformat(), "end": end.isoformat(), "power_w": 0},
        {"start": end.isoformat(), "end": (end + quarter).isoformat(), "power_w": -1500},
        {"start": (end + quarter).isoformat(), "end": (end + 2 * quarter).isoformat(), "power_w": 2000},
    ]

    hass.states.async_set("sensor.ev_plan", "unavailable", {"intervals": []})
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == "Unavailable"
    assert state.attributes["external_ev_schedule"] == []

    hass.states.async_set("sensor.ev_plan", "active", {"intervals": [
        {"start": (current_quarter - quarter).isoformat(), "end": current_quarter.isoformat(), "power_w": 1500},
    ]})
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == "Expired"
    assert state.attributes["external_ev_schedule"] == []

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not frontend.async_panel_exists(hass, "lahems2-overview")
