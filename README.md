# LAHEMS2

<img src="custom_components/lahems2/brand/icon.png" alt="LAHEMS2" width="96">

LAHEMS2 connects Home Assistant measurements for one heating zone, electricity use,
and optional solar, pellet heat, home batteries, and an EV. The integration provides an English setup wizard and **Data Status**, **Actual**,
and **Proposed** sensors. It runs in **Shadow Mode** and only reads Home Assistant state.

## Install on a test instance

The verification target is Home Assistant Core **2026.11.1**. To try this
development build, copy `custom_components/lahems2` into the test instance's
`config/custom_components` directory and restart Home Assistant. Open
**Settings → Devices & services → Add integration**, then select **LAHEMS2**.

After a public GitHub release is available, use **Settings → Marketplace → Add
from link**. Connect GitHub if prompted, enter
`https://github.com/johannesdeboer/lahems2`, and install the integration.
Start the same setup wizard from **Add integration**.

## Set up and edit your profile

1. Choose a preferred Home Assistant area if you want area suggestions. LAHEMS2
   checks the entity's area first and uses the device's area when the entity
   has none. It ranks likely room-air sensors ahead of equipment sensors.
   Confirm the actual room sensor. The **All areas** step includes unassigned
   temperature sensors and lets you replace the suggested sensor or add a backup.
   Set the **Temperature freshness limit** for all temperature inputs.
2. Select the heat pump thermostat. LAHEMS2 reads its current setpoint for the
   comfort baseline. Add separate power or energy measurement entities where
   available. **Pellet Heat Request** accepts a switch that reports **On** or
   **Off**, such as an ESPHome relay. A separate heat-demand entity and a
   temperature sensor near the stove are optional.
3. Confirm one grid power or energy sensor. Review any suggested price and
   solar sources. Suggestions use Home Assistant device classes and entity
   names, so confirm each against the measurement you want. Use separate import
   and export price entities where rates differ, or select the same entity when
   one published rate applies to both. Set the **Tariff freshness limit** to match
   how often the price entities report. You can also add
   an external EV power source and schedule. For each battery, specify its
   power sign convention and the controller that owns its setpoints.
4. Review the selected entities, readings and availability, then save. Open
   **Settings → Devices & services → LAHEMS2 → Configure** to edit any binding,
   change the preferred area, or add or remove the pellet switch or batteries.

## Read Data Status

Open **Data Status** from the integration's entities. Its attributes show each
role's selected entity, latest reading, unit, availability and whether that
reading can be used. Inspect the complete attributes under **Developer Tools →
States**. The sensor shows **Ready** if all required or configured sources
are readable and all watched bindings match their configured area and entity
ID. **Check inputs** means at least one role needs attention.

| Status | Meaning |
| --- | --- |
| Available | The entity has a usable reading for this role. |
| Not configured | The role is optional and has no binding. |
| Missing source | A required source or an import/export price is unbound. Other valid inputs remain usable. |
| Unavailable / Unknown | Home Assistant has no usable current state for the bound entity. |
| Entity disabled / removed / missing | The binding needs attention in the entity registry or in **Configure**. |
| Moved to a different area / Entity renamed | The identity or area changed after setup. A valid reading continues to be used. Save the binding again to record the new area or name. |
| Stale reading | Time since Home Assistant last reported the temperature or tariff has reached its configured limit. |
| Unexpected entity type / Invalid reading | The entity no longer matches the role or its reading is invalid. |

The active indoor reading comes from the primary sensor when usable and falls
back to the explicitly configured backup. If both are unusable, the active
reading is empty. A temperature, heat-pump reading, or other valid input
remains visible when a price source is missing. Diagnostics use the latest
reported state. Temperature and tariff freshness limits both default to 15 minutes.
Set them independently in **Configure** to suit your sources. A sensor that only
reports when its value changes can go stale even if the value remains correct.
Data Status checks for staleness every minute and when a bound entity changes.
Forecast and schedule bindings report availability and leave
interpretation of future values to the planner.

## View Actual and Proposed

Open the LAHEMS2 integration in **Settings → Devices & services** and select
**Actual** or **Proposed**. In **Developer Tools → States**, you can see
their full attributes. For an at-a-glance view, edit a Home Assistant dashboard,
choose **Add card → Manual**, and paste the
[Shadow Mode card YAML](docs/shadow-mode-card.yaml). Use your actual entity
IDs if Home Assistant assigned different ones.

**Actual** displays the active indoor temperature, thermostat setpoint and
readings from your configured sources. **Proposed** explains the current
comfort check and, when configured, compares the current import tariff to the
pellet threshold. Enter the threshold in your Home Assistant currency per kWh.
Configure the ESPHome pellet switch to enable this comparison. **Review pellet heat**
is a prompt for manual review. The proposal does not model pellet budget,
shared-circuit constraints, future tariffs, PV forecasts, or EV schedules.

## Read Energy Balance

Open **Settings → Devices & services → LAHEMS2 → Configure** to add the
optional EV connection, charge level, departure timestamp and required
departure charge. Set the grid and EV power signs for your meters and enter
import and export limits in watts if known. Existing profiles default to
positive grid import and positive EV charging. Batteries keep their own
configured sign convention.

**Energy Balance** shows grid, solar, EV and stationary battery power
separately. Positive storage power means charging. The unallocated load is
grid plus measured PV minus measured EV and battery power. It includes any
unmetered loads or generation. It is unknown if the grid source is an energy
counter or a configured flow has no usable power reading.

For a future EV plan, select a Home Assistant sensor whose `intervals` attribute
contains rows such as:

```yaml
intervals:
  - start: "2027-01-01T02:00:00+00:00"
    end: "2027-01-01T02:30:00+00:00"
    power_w: 2000
```

The schedule uses the configured EV sign. LAHEMS2 averages power over each
fully covered 15-minute interval for up to 48 hours. Gaps remain unknown.
Expired schedules and disconnected EVs supply no schedule intervals.
A current-power setpoint or a calendar state alone supplies no future plan.
The external controller continues to own the EV schedule. V2G Liberty 0.9.0
can supply this format through the [accepted schedule export](docs/v2g-liberty-schedule.md).

To uninstall, remove the LAHEMS2 integration entry in **Devices & services**
and then remove the component through the Marketplace or remove the manually
copied component folder and restart. Configuration is stored with the Home
Assistant entry and survives integration updates.

## Energy planning roadmap

See the [energy planning roadmap](docs/energy-planning-roadmap.md) for
stationary battery optimisation, bidirectional EVs and external controllers.

## GitHub testing and release

On GitHub, open **Actions → Validate LAHEMS2**. The workflow runs the Home
Assistant Core integration tests and Hassfest. When the test job passes,
download **lahems2-test-install** from the run's artifacts. Extract it and use
the manual test-instance installation above. The HACS Action runs when the
repository is public and can be validated for Marketplace.

Run the Core integration tests in a Python 3.14.2 or newer environment.

```sh
python -m pip install -r requirements-test.txt
python -c "import homeassistant; import pytest; raise SystemExit(pytest.main())"
```

Run the standalone validation and fallback tests with
`python -m unittest discover -s tests -p test_model.py`.

Before publishing a release:

- Run the Core 2026.11.1 tests, Hassfest, and the HACS Action. Set a GitHub
  repository description and topics and enable Issues for HACS validation.
- On a separate Home Assistant 2026.11.1 instance, install cleanly and complete
  setup with and without a pellet switch. Exercise the area picker, cross-area
  selection, **Configure**, and Data Status in the browser. Replace the
  installed build with the release candidate, restart, and confirm that
  bindings, entity identity, and Shadow Mode persist.
- Confirm the MIT license for publication and review any future code or
  fixtures brought over from private prototypes. The tests in this milestone
  use synthetic entities.
- Match the version in `manifest.json` to the release tag,
  publish the GitHub release, and verify installation from the repository URL
  through Marketplace. Catalog listing is optional.

[Marketplace custom repositories](https://www.home-assistant.io/integrations/marketplace/#custom-repositories)
· [Publishing a custom integration](https://contributors.home-assistant.io/marketplace/guides/integrations/)
