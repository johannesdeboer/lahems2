# V2G Liberty accepted EV schedule

V2G Liberty 0.9.0 installs the accepted FlexMeasures plan as charger timers.
Its Home Assistant entities expose the current setpoint, a state-of-charge
projection and a FlexMeasures connection status. LAHEMS2 needs the actual
planned power values and their time boundaries.

The [export patch](v2g-liberty-v0.9.0-schedule-export.patch) adds
`sensor.v2g_liberty_accepted_ev_schedule` to the
[V2G Liberty 0.9.0 source](https://github.com/V2G-liberty/addon-v2g-liberty/tree/v0.9.0).
V2G Liberty publishes the plan after it has accepted the schedule and installed
its timers. The sensor contains UTC start and end timestamps and signed watts.
Positive power charges the EV, and negative power discharges it. An expired plan
supplies no quarters. A restart, timer cancellation or discharge refusal clears
the exported plan. The next accepted plan replaces it.

## Use the export

The stock V2G Liberty app does not include the export. Apply the patch to a
source checkout at tag `v0.9.0` and build an app image from that source, or use
a V2G Liberty release that includes an equivalent export. Installing LAHEMS2
alone does not update the V2G Liberty app.

```sh
git apply --check /path/to/lahems2/docs/v2g-liberty-v0.9.0-schedule-export.patch
git apply /path/to/lahems2/docs/v2g-liberty-v0.9.0-schedule-export.patch
```

In **Settings → Devices & services → LAHEMS2 → Configure**, select
`sensor.v2g_liberty_accepted_ev_schedule` as the **Scheduled EV power from the external controller**
and use the default positive-charging EV sign. Select the charger power
measurement separately. When V2G Liberty is in Automatic mode with a connected
EV and has accepted a plan, check **Energy Balance** for
`external_ev_schedule`. LAHEMS2 averages fully covered quarters and leaves
gaps without a setpoint. V2G Liberty remains in control of the charger.

After an abrupt app stop, the last exported state can remain visible in Home
Assistant until its interval horizon expires or the app starts again and clears
it. Treat these intervals as Shadow Mode planning inputs.

Other bidirectional controllers can publish their accepted plan to any
Home Assistant sensor using the [same interval format](../README.md#read-energy-balance).
