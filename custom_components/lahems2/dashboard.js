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
  .schedule { margin-top: 12px; }
  dl { margin: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(105px, 1fr)); gap: 12px; }
  dt { color: var(--secondary-text-color, #465753); }
  dd { margin: 2px 0 0; font-size: 17px; font-weight: 600; }
  .schedule-head { display: flex; flex-wrap: wrap; align-items: start; gap: 8px 16px; justify-content: space-between; }
  select { font: inherit; color: inherit; background: inherit; border: 1px solid var(--divider-color, #b2bfba); border-radius: 6px; padding: 6px; }
  .schedule dl { margin: 14px 0; }
  .table { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; margin-top: 12px; }
  td, th { text-align: start; padding: 8px 7px; border-bottom: 1px solid var(--divider-color, #d3dfda); }
  td:last-child, th:last-child { text-align: end; white-space: nowrap; }
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
    this.attention = element("section", undefined, "attention");
    main.append(intro, cards, schedule, this.attention);
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
    this.renderOverview();
    this.renderSchedule();
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
      ]),
      element("p", `Proposal: ${usableState(proposed, "Unavailable")}`),
      element("p", proposed?.attributes.reason || "", "detail"),
    );
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
    this.schedule.replaceChildren(element("p", state, "detail"));
    const windows = balance?.attributes.external_ev_schedule;
    if (!Array.isArray(windows) || !windows.length) return;
    const now = Date.now();
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
      this.schedule.append(element("p", "Times outside the listed intervals have no published power target.", "detail"));
    }
    const table = element("table");
    const head = table.createTHead().insertRow();
    for (const label of ["Start", "End", "Scheduled power"]) {
      const th = element("th", label);
      th.scope = "col";
      head.append(th);
    }
    const body = table.createTBody();
    for (const row of rows) {
      const tr = body.insertRow();
      tr.append(
        element("td", this.clock.format(row.start)),
        element("td", this.clock.format(row.end)),
        element("td", this.flow(row.power, "Charge", "Discharge")),
      );
    }
    const container = element("div", undefined, "table");
    container.append(table);
    this.schedule.append(container);
  }
}

customElements.define("lahems2-overview-panel", Lahems2Overview);
