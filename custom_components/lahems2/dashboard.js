const VIEWS = ["actual", "proposed", "energy_balance", "data_status"];

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function usableState(entity, fallback) {
  return entity && !["unknown", "unavailable"].includes(entity.state)
    ? entity.state
    : fallback;
}

const STYLES = `
  :host {
    display: block; height: 100%; overflow: auto;
    color: var(--primary-text-color, #172421);
    background: var(--primary-background-color, #f2f5f3);
  }
  * { box-sizing: border-box; }
  header {
    display: flex; align-items: center; gap: 12px; padding: 0 20px; min-height: 56px;
    color: var(--app-header-text-color, var(--primary-text-color, #172421));
    background: var(--app-header-background-color, var(--card-background-color, #fff));
  }
  header strong { font-size: 20px; }
  header a { color: inherit; margin-inline-start: auto; }
  main { max-width: 1080px; margin: auto; padding: clamp(12px, 3vw, 32px); }
  .intro { border-inline-start: 4px solid var(--primary-color, #00796b); padding-inline-start: 16px; margin-bottom: 20px; }
  h1, h2, p { margin: 0; }
  h1 { font-size: clamp(25px, 4vw, 34px); line-height: 1.15; margin-bottom: 6px; }
  h2 { font-size: 19px; margin-bottom: 10px; }
  p, li { line-height: 1.5; }
  .detail { color: var(--secondary-text-color, #465753); }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 250px), 1fr)); gap: 12px; }
  section {
    min-width: 0; padding: clamp(14px, 2vw, 20px);
    background: var(--card-background-color, #fff);
    border: 1px solid var(--divider-color, #d3dfda); border-radius: 12px;
  }
  .schedule, .shadow-plan { margin-top: 12px; }
  dl { margin: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(105px, 1fr)); gap: 12px; }
  dt { color: var(--secondary-text-color, #465753); }
  dd { margin: 2px 0 0; font-size: 17px; font-weight: 600; }
  .schedule-head { display: flex; flex-wrap: wrap; align-items: start; gap: 8px 16px; justify-content: space-between; }
  select { font: inherit; color: inherit; background: inherit; border: 1px solid var(--divider-color, #b2bfba); border-radius: 6px; padding: 6px; }
  .schedule dl { margin: 14px 0; }
  .shadow-plan dl { margin-block: 12px; }
  .schedule-chart { margin-top: 14px; }
  .chart-grid {
    direction: ltr; display: grid; grid-template-columns: auto minmax(0, 1fr);
    grid-template-rows: 126px auto; gap: 4px 8px;
    font-size: 12px; color: var(--secondary-text-color, #465753);
    font-variant-numeric: tabular-nums;
  }
  .chart-scale { display: flex; flex-direction: column; justify-content: space-between; text-align: end; }
  .chart-plot {
    position: relative; border-block: 1px solid var(--divider-color, #d3dfda);
    background: var(--secondary-background-color, var(--primary-background-color, #f2f5f3));
    cursor: crosshair;
  }
  .chart-plot:focus-visible { outline: 2px solid var(--primary-color, #00796b); outline-offset: 3px; }
  .chart-zero { position: absolute; inset: 50% 0 auto; border-top: 1px solid currentColor; }
  .chart-bar { position: absolute; top: 50%; }
  .charge { background: var(--energy-battery-in-color, #00897b); }
  .discharge { background: var(--energy-battery-out-color, #d4622f); }
  .chart-idle { background: currentColor; }
  .chart-marker {
    position: absolute; inset-block: 0; border-inline-start: 1px dashed currentColor;
    pointer-events: none;
  }
  .chart-time { grid-column: 2; display: flex; justify-content: space-between; gap: 4px; }
  .chart-legend { display: flex; flex-wrap: wrap; gap: 4px 14px; }
  .chart-legend span::before {
    content: ""; display: inline-block; width: 9px; height: 9px; margin-inline-end: 5px;
    border-radius: 2px;
  }
  .chart-legend .legend-charge::before { background: var(--energy-battery-in-color, #00897b); }
  .chart-legend .legend-discharge::before { background: var(--energy-battery-out-color, #d4622f); }
  .chart-reading { min-height: 2.8em; margin-top: 4px; }
  @media (max-width: 480px) {
    .chart-time span:nth-child(even) { display: none; }
  }
  .attention { margin-top: 12px; }
  ul { margin: 8px 0 0; padding-inline-start: 20px; }
`;

class Lahems2Overview extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: "open"});
    this.hours = 6;
    this.sources = {};
    this.cards = {};
    const style = element("style", STYLES);
    const header = element("header");
    this.menu = element("ha-menu-button");
    this.menu.label = "Open Home Assistant menu";
    this.settings = element("a", "Configure LAHEMS2");
    this.settings.href = "/config/integrations/integration/lahems2";
    this.settings.hidden = true;
    header.append(this.menu, element("strong", "LAHEMS2"), this.settings);
    const main = element("main");
    const intro = element("div", undefined, "intro");
    intro.append(
      element("h1", "Energy overview"),
      element("p", "Shadow Mode · Monitoring and planning only", "detail"),
    );
    const cards = element("div", undefined, "cards");
    for (const [key, label] of [["flow", "Power now"], ["heat", "Heating"], ["ev", "Electric vehicle"]]) {
      const section = element("section");
      this.cards[key] = element("div");
      section.append(element("h2", label), this.cards[key]);
      cards.append(section);
    }
    this.pelletCard = element("section");
    this.pelletCard.hidden = true;
    cards.append(this.pelletCard);
    const schedule = element("section", undefined, "schedule");
    const scheduleHead = element("div", undefined, "schedule-head");
    this.horizon = element("select");
    this.horizon.setAttribute("aria-label", "EV schedule time range");
    for (const hours of [6, 24]) {
      const option = element("option", `Next ${hours} hours`);
      option.value = String(hours);
      this.horizon.append(option);
    }
    this.horizon.addEventListener("change", () => {
      this.hours = Number(this.horizon.value);
      this.renderSchedule();
    });
    scheduleHead.append(element("h2", "Accepted EV schedule"), this.horizon);
    this.schedule = element("div");
    schedule.append(scheduleHead, this.schedule);
    this.shadowPlan = element("section", undefined, "shadow-plan");
    this.shadowPlan.hidden = true;
    this.attention = element("section", undefined, "attention");
    main.append(intro, cards, schedule, this.shadowPlan, this.attention);
    this.shadowRoot.append(style, header, main);
  }

  set narrow(value) {
    this.menu.narrow = value;
  }

  set hass(hass) {
    this.menu.hass = hass;
    this.settings.hidden = !hass.user?.is_admin;
    const previous = this.sources;
    const sources = {};
    const missing = new Set(VIEWS);
    for (const view of VIEWS) {
      const entity = hass.states[previous[view]?.entity_id];
      if (entity?.attributes.lahems2_view === view) {
        sources[view] = entity;
        missing.delete(view);
      }
    }
    if (missing.size) {
      for (const entity of Object.values(hass.states)) {
        const view = entity.attributes.lahems2_view;
        if (missing.delete(view)) sources[view] = entity;
        if (!missing.size) break;
      }
    }
    const language = hass.locale?.language ?? hass.language;
    const formatKey = `${language}/${hass.config.time_zone}`;
    if (formatKey === this.formatKey && VIEWS.every((view) => previous[view] === sources[view])) return;
    this.sources = sources;
    this.formatKey = formatKey;
    this.number = new Intl.NumberFormat(language, {maximumFractionDigits: 2});
    this.clock = new Intl.DateTimeFormat(language, {
      timeZone: hass.config.time_zone, month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit", timeZoneName: "short",
    });
    this.axisClock = new Intl.DateTimeFormat(language, {
      timeZone: hass.config.time_zone, hour: "2-digit", minute: "2-digit",
    });
    this.renderOverview();
    this.renderSchedule();
    this.renderShadowPlan();
  }

  quantity(value, unit) {
    if (value == null || value === "" || !Number.isFinite(Number(value))) return "Unavailable";
    return `${this.number.format(value)} ${unit}`.trim();
  }

  flow(watts, positive, negative) {
    if (!Number.isFinite(watts)) return "Unavailable";
    if (watts === 0) return "Idle · 0 kW";
    return `${watts > 0 ? positive : negative} · ${this.quantity(Math.abs(watts) / 1000, "kW")}`;
  }

  metrics(rows) {
    const list = element("dl");
    for (const [label, value] of rows) {
      const item = element("div");
      item.append(element("dt", label), element("dd", value));
      list.append(item);
    }
    return list;
  }

  renderOverview() {
    const {actual, proposed, energy_balance: balance, data_status: status} = this.sources;
    const power = balance?.attributes ?? {};
    const heat = actual?.attributes ?? {};
    const batteries = Array.isArray(power.batteries) ? power.batteries : [];
    this.cards.flow.replaceChildren(this.metrics([
      ["Grid", this.flow(power.grid_w, "Importing", "Exporting")],
      ["Solar", this.quantity(power.pv_w == null ? null : power.pv_w / 1000, "kW")],
      ...batteries.map((battery, index) => [
        `Home battery ${index + 1}`, this.flow(battery.power_w, "Charging", "Discharging"),
      ]),
    ]));
    this.cards.heat.replaceChildren(
      this.metrics([
        ["Indoor temperature", this.quantity(heat.indoor_temperature, heat.indoor_temperature_unit || "")],
        ["Setpoint", this.quantity(heat.comfort_target, heat.comfort_target_unit || "")],
        ["Heat pump electrical power", this.quantity(heat.heat_pump_power_w == null ? null : heat.heat_pump_power_w / 1000, "kW")],
      ]),
      element("p", `Proposal: ${usableState(proposed, "Unavailable")}`),
      element("p", proposed?.attributes.reason || "", "detail"),
    );
    const pellet = heat.pellet_stove;
    this.pelletCard.hidden = !pellet;
    if (pellet) {
      const rooms = Object.values(pellet.rooms);
      this.pelletCard.replaceChildren(
        element("h2", "Pellet stove"),
        element("p", pellet.status, "detail"),
        this.metrics([
          ["Estimated fuel stock", this.quantity(pellet.stock_kg, "kg")],
          ...rooms.map((room) => [
            room.name,
            room.comfort_target != null ? `Setpoint · ${this.quantity(room.comfort_target, room.comfort_target_unit || "")}`
              : room.usable ? this.quantity(room.reading, room.unit || "") : room.status,
          ]),
        ]),
      );
    }
    this.cards.ev.replaceChildren(this.metrics([
      ["Connection", power.ev_connected === true ? "Connected" : power.ev_connected === false ? "Disconnected" : "Unknown"],
      ["State of charge", this.quantity(power.ev_soc_percent, "%")],
      ["Charger power", this.flow(power.ev_w, "Charging", "Discharging")],
    ]));
    const issues = Object.entries(status?.attributes.roles ?? {})
      .filter(([, reading]) => !["Available", "Not configured"].includes(reading.status));
    const list = element("ul");
    for (const [name, reading] of issues) list.append(element("li", `${name}: ${reading.status}`));
    this.attention.replaceChildren(element("h2", "Data status"), element("p", usableState(status, "Waiting for LAHEMS2")));
    if (issues.length) this.attention.append(list);
  }

  renderSchedule() {
    const balance = this.sources.energy_balance;
    if (!this.clock) return;
    const state = usableState(balance, "Schedule source unavailable");
    const windows = balance?.attributes.external_ev_schedule;
    const now = Date.now();
    const key = JSON.stringify([this.formatKey, this.hours, Math.floor(now / 60_000), state, windows]);
    if (key === this.scheduleKey) return;
    this.scheduleKey = key;
    const hadFocus = this.shadowRoot.activeElement?.classList.contains("chart-plot");
    this.schedule.replaceChildren(element("p", state, "detail"));
    if (!Array.isArray(windows) || !windows.length) return;
    const until = now + this.hours * 3_600_000;
    const rows = [];
    let charge = 0;
    let discharge = 0;
    let covered = 0;
    for (const entry of windows) {
      const start = Date.parse(entry.start);
      const end = Date.parse(entry.end);
      const length = Math.min(end, until) - Math.max(start, now);
      if (!(length > 0) || !Number.isFinite(entry.power_w)) continue;
      rows.push({start, end, power: entry.power_w});
      covered += length;
      const kwh = entry.power_w * length / 3_600_000_000;
      charge += Math.max(0, kwh);
      discharge += Math.max(0, -kwh);
    }
    if (!rows.length) {
      this.schedule.append(element("p", "No schedule intervals in this time range."));
      return;
    }
    this.schedule.append(this.metrics([
      ["Scheduled charging", this.quantity(charge, "kWh")],
      ["Scheduled discharging", this.quantity(discharge, "kWh")],
      ["Plan coverage", `${this.quantity(covered / 3_600_000, "h")} of ${this.hours} h`],
    ]));
    if (covered < until - now) {
      this.schedule.append(element("p", "Blank intervals have no published power target.", "detail"));
    }
    this.renderScheduleChart(rows, now, until, hadFocus);
  }

  renderScheduleChart(rows, start, end, restoreFocus) {
    const chart = element("div", undefined, "schedule-chart");
    const grid = element("div", undefined, "chart-grid");
    const scale = Math.max(1, Math.ceil(Math.max(...rows.map((row) => Math.abs(row.power))) / 1000));
    const labels = element("div", undefined, "chart-scale");
    for (const value of [scale, 0, -scale]) labels.append(element("span", this.quantity(value, "kW")));
    const plot = element("div", undefined, "chart-plot");
    plot.tabIndex = 0;
    plot.setAttribute("role", "group");
    plot.setAttribute("aria-label", "Scheduled EV power. Use the arrow keys to inspect quarter-hour targets.");
    plot.append(element("span", undefined, "chart-zero"));
    const duration = end - start;
    const position = (time) => (time - start) / duration * 100;
    for (const row of rows) {
      const kind = row.power > 0 ? "charge" : row.power < 0 ? "discharge" : "chart-idle";
      const bar = element("span", undefined, `chart-bar ${kind}`);
      const height = Math.abs(row.power) / (scale * 1000) * 50;
      const left = position(Math.max(row.start, start));
      bar.style.left = `${left}%`;
      bar.style.width = `${position(Math.min(row.end, end)) - left}%`;
      bar.style.height = row.power === 0 ? "2px" : `${height}%`;
      bar.style.top = `${50 - (row.power > 0 ? height : 0)}%`;
      plot.append(bar);
    }
    const marker = element("span", undefined, "chart-marker");
    plot.append(marker);
    const ticks = element("div", undefined, "chart-time");
    for (let i = 0; i <= 4; i++) {
      ticks.append(element("span", this.axisClock.format(start + i * duration / 4)));
    }
    const legend = element("div", undefined, "chart-legend detail");
    legend.append(element("span", "Charging", "legend-charge"), element("span", "Discharging", "legend-discharge"));
    const reading = element("p", undefined, "chart-reading");
    reading.setAttribute("role", "status");
    const quarter = 15 * 60_000;
    const inspect = (time) => {
      this.inspectedScheduleTime = Math.max(start, Math.min(end - 1, time));
      const row = rows.find(({start, end}) => start <= this.inspectedScheduleTime && this.inspectedScheduleTime < end);
      const from = row?.start ?? Math.floor(this.inspectedScheduleTime / quarter) * quarter;
      const to = row?.end ?? from + quarter;
      marker.style.left = `${position(Math.max(start, from))}%`;
      const power = row ? this.flow(row.power, "Charge", "Discharge") : "No published target";
      const detail = `${this.clock.format(from)} – ${this.axisClock.format(to)} · ${power}`;
      if (reading.textContent !== detail) reading.textContent = detail;
    };
    const inspectPointer = (event) => {
      const bounds = plot.getBoundingClientRect();
      if (bounds.width) inspect(start + (event.clientX - bounds.left) / bounds.width * duration);
    };
    plot.addEventListener("pointermove", (event) => {
      if (event.pointerType === "mouse") inspectPointer(event);
    });
    plot.addEventListener("click", inspectPointer);
    plot.addEventListener("keydown", (event) => {
      const current = this.inspectedScheduleTime;
      const times = {
        ArrowLeft: current - quarter, ArrowRight: current + quarter,
        Home: start, End: end - 1,
      };
      if (!Object.hasOwn(times, event.key)) return;
      event.preventDefault();
      inspect(times[event.key]);
    });
    inspect(this.inspectedScheduleTime ?? Math.max(start, rows[0].start));
    grid.append(labels, plot, ticks);
    chart.append(grid, legend, reading);
    this.schedule.append(chart);
    if (restoreFocus) plot.focus({preventScroll: true});
  }

  renderShadowPlan() {
    const plan = this.sources.energy_balance?.attributes.shadow_plan;
    this.shadowPlan.hidden = !plan;
    if (!plan) return;
    const key = JSON.stringify([this.formatKey, plan]);
    if (key === this.shadowPlanKey) return;
    this.shadowPlanKey = key;
    this.shadowPlan.replaceChildren(
      element("h2", "Shadow plan comparison"),
      element("p", plan.reason || plan.status, "detail"),
    );
    if (plan.baseline_cost == null) return;
    const savings = plan.proposed_cost == null ? null : plan.baseline_cost - plan.proposed_cost;
    this.shadowPlan.append(this.metrics([
      ["Baseline cost", this.quantity(plan.baseline_cost, plan.currency)],
      ["Proposed cost", this.quantity(plan.proposed_cost, plan.currency)],
      ["Estimated savings", this.quantity(savings, plan.currency)],
      ...(plan.pellet_cost == null ? [] : [["Proposed pellet cost", this.quantity(plan.pellet_cost, plan.currency)]]),
    ]));
    const intervals = plan.intervals;
    if (!intervals?.length) return;
    this.shadowPlan.append(element(
      "p",
      `${this.quantity(intervals.length / 4, "h")} of forecast coverage · ${this.quantity(plan.round_trip_efficiency_percent, "%")} round-trip efficiency`,
      "detail",
    ));
    const select = element("select");
    select.setAttribute("aria-label", "Inspect a planning interval");
    const details = element("div");
    for (const interval of intervals) {
      const option = element("option", this.clock.format(Date.parse(interval.start)));
      option.value = interval.start;
      select.append(option);
    }
    if (intervals.some((interval) => interval.start === this.inspectedPlanStart)) select.value = this.inspectedPlanStart;
    const inspect = () => {
      const interval = intervals[select.selectedIndex];
      this.inspectedPlanStart = interval.start;
      details.replaceChildren(this.metrics([
        ["Baseline grid", this.flow(interval.baseline_grid_w, "Importing", "Exporting")],
        ["Proposed grid", this.flow(interval.proposed_grid_w, "Importing", "Exporting")],
        ["Fixed EV power", this.flow(interval.ev_power_w, "Charging", "Discharging")],
        ...(interval.heat_pump_baseline_w == null ? [] : [
          ["Baseline heat pump", this.quantity(interval.heat_pump_baseline_w / 1000, "kW")],
          ["Proposed heat pump", this.quantity(interval.heat_pump_proposed_w / 1000, "kW")],
        ]),
        ...(interval.pellet_remaining_kg == null ? [] : [
          ["Pellets used", this.quantity(interval.pellet_used_kg, "kg")],
          ["Projected pellet stock", this.quantity(interval.pellet_remaining_kg, "kg")],
        ]),
        ...interval.battery_power_w.map((power, index) => [
          `Home battery ${index + 1}`,
          `${this.flow(power, "Charge", "Discharge")} · ${this.quantity(interval.battery_end_soc_percent[index], "%")} at end`,
        ]),
      ]));
    };
    select.addEventListener("change", inspect);
    inspect();
    this.shadowPlan.append(select, details);
  }
}

customElements.define("lahems2-overview-panel", Lahems2Overview);
