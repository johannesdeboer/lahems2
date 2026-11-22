# Energy planning roadmap

LAHEMS2 will optimise heating, stationary batteries and bidirectional EV use
against the same household energy balance. The first release of this planner
will compare schedules in Shadow Mode.

## Current integration

| Asset | LAHEMS2 inputs today | Example controller |
| --- | --- | --- |
| Heat pump and pellet stove | Thermostat, measurements and optional pellet request switch | Existing thermostat and appliance controls |
| Stationary battery | Power, state of charge, configured capacity, reserve, charge and discharge limits, power sign and controller name | Battery manufacturer's energy management integration |
| Bidirectional EV | Measured power and an optional generic schedule entity | V2G Liberty for a compatible bidirectional EV and charger |

Select V2G Liberty's measured charger power for **Measured EV power**.
Its current scheduled power sensor reports one setpoint. A full plan requires
a timestamped series via the [external schedule input](../README.md#read-energy-balance).
The [V2G Liberty schedule export](v2g-liberty-schedule.md) publishes that series
once V2G Liberty has accepted the plan.

The LAHEMS2 sidebar panel shows the accepted EV schedule, the planned charging
and discharging energy, and how much of the selected time range the plan covers.
This makes the plan available for inspection before it is used in optimisation.

## Milestones

1. **Current observations and heating proposals.** Show the measurements,
   comfort check and current tariff comparison on one Home Assistant card.
2. **External schedules and household balance.** Read EV and battery power
   separately from PV and net grid power. Normalize power signs and timestamped
   inputs into 15-minute intervals. Add EV connection state, state of charge,
   departure deadline and required charge, plus battery limits and applicable
   import/export constraints. LAHEMS2 exposes valid external EV quarters
   as fixed inputs for the joint optimiser in milestone 3.
3. **Joint optimisation in Shadow Mode.** Compare the baseline and proposed
   schedules for heating, stationary battery charge/discharge, and EV
   charge/discharge when the vehicle is connected and available. Use import and
   export tariffs, PV and demand forecasts, storage efficiency and reserve
   limits. Show planned power, projected state of charge, expected import/export
   costs and the constraints that drove the plan. Test that an expensive
   export interval does not trigger stationary battery charging from the EV. Allow
   intentional transfers only when explicitly modelled and enabled, and account
   for conversion losses.
4. **Optional execution.** Assign a single writer for each asset. Keep V2G
   Liberty as the EV charger writer while it owns the EV schedule. Apply stationary
   battery setpoints only through a verified control interface, with an explicit
   opt-in and tests for stale inputs, controller conflicts and fallback. Verify
   stationary battery control on the target hardware before promising automatic enforcement.

## Reuse candidates

- [V2G Liberty](https://github.com/V2G-liberty/addon-v2g-liberty) already uses
  FlexMeasures for bidirectional EV scheduling. Reuse its measured power and
  schedule where they are exposed. Test full schedule availability separately
  from the current power setpoint.
- [Day Ahead Optimizer](https://github.com/corneel27/day-ahead) already
  optimises stationary batteries, EV charging and other loads. Its
  [configuration documentation](https://github.com/corneel27/day-ahead/blob/main/dao/DOCS.md)
  describes 15-minute prices. Evaluate its schedule export and battery control
  on supported hardware before choosing whether to delegate stationary battery
  scheduling to DAO. Keep the bidirectional charger under its existing controller when evaluating DAO.
