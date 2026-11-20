/*
 * Grad no! – Abfahrtstafel-Karte für Home Assistant
 * https://github.com/emils1988/grad-no-homeassistant
 *
 * Zeigt die Abfahrten eines Grad-no!-Sensors im Look der Anzeige
 * (Themes Standard, ÖBB, Wiener Linien). Tipp auf einen Fernverkehrszug zeigt die Wagenreihung.
 */

const CARD_VERSION = "0.1.0";

const THEMES = {
  standard: { bg: "#000000", alt: "#0e0f14", header: "#161822", text: "#ffffff", accent: "#ffb000", sub: "#8c8c96" },
  oebb: { bg: "#1b2a80", alt: "#22338f", header: "#0e1a5c", text: "#ffffff", accent: "#ffe14d", sub: "#b8c2ee" },
  wl: { bg: "#000000", alt: "#1e1e1e", header: "#4a4a4c", text: "#ffffff", accent: "#ffb400", sub: "#a0a0a0" },
};

const KIND_COLORS = { train: "#aa1923", sbahn: "#0064b9", bus: "#4b4e5c", tram: "#c82828", metro: "#6e3ca0" };
const U_COLORS = { U1: "#e3000f", U2: "#a05fa5", U3: "#eb7819", U4: "#1e9646", U5: "#0096a0", U6: "#a06428" };
const RED = "#ff463c";
const GREEN = "#50c86e";
const YELLOW = "#ffd200";

const TEXT = {
  de: {
    now: "jetzt",
    cancelled: "fällt aus",
    none: "Keine Abfahrten",
    unavailable: "Keine Verbindung zur Fahrplanauskunft",
    missing: "Sensor nicht gefunden",
    platform: "Gleis",
    formation: "Wagenreihung",
    loading: "Lade Wagenreihung …",
    close: "Schließen",
    first: "1. Klasse",
    business: "Business",
    dining: "Speisewagen",
    quiet: "Ruhezone",
    family: "Familie",
    bikes: "Fahrräder",
    wheelchair: "Rollstuhl",
    load: "Auslastung",
    direction: "Fahrtrichtung",
    to: "nach",
  },
  en: {
    now: "now",
    cancelled: "cancelled",
    none: "No departures",
    unavailable: "No connection to the timetable service",
    missing: "Sensor not found",
    platform: "Platform",
    formation: "Coach formation",
    loading: "Loading coach formation …",
    close: "Close",
    first: "1st class",
    business: "Business",
    dining: "Dining car",
    quiet: "Quiet zone",
    family: "Family",
    bikes: "Bicycles",
    wheelchair: "Wheelchair",
    load: "Occupancy",
    direction: "Direction of travel",
    to: "to",
  },
};

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function badgeColor(d) {
  if (d.kind === "metro" && U_COLORS[d.line]) return U_COLORS[d.line];
  return KIND_COLORS[d.kind] || KIND_COLORS.train;
}

// "Bus 13A" -> "13A", "Tram O" -> "O" (wie am Display: der Badge zeigt die Art schon über die Farbe)
function badgeText(d) {
  const l = String(d.line || "");
  if (d.kind === "bus" || d.kind === "tram") return l.replace(/^(bus|tram|str|ast|nachtbus|citybus|rufbus|o-bus|obus)\s+/i, "");
  return l;
}

class GradNoCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._index = 0;
    this._formation = null;
    this._lastKey = "";
  }

  static getStubConfig(hass) {
    const ent = Object.keys(hass?.states || {}).find((e) => {
      const a = hass.states[e].attributes || {};
      return e.startsWith("sensor.") && Array.isArray(a.departures) && "provider" in a;
    });
    return { entities: ent ? [ent] : [] };
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "entities", required: true, selector: { entity: { multiple: true, filter: { integration: "grad_no", domain: "sensor" } } } },
        { name: "title", selector: { text: {} } },
        {
          type: "grid",
          name: "",
          schema: [
            {
              name: "theme",
              selector: {
                select: {
                  mode: "dropdown",
                  options: [
                    { value: "auto", label: "Automatisch / Auto" },
                    { value: "standard", label: "Standard (Amber)" },
                    { value: "oebb", label: "ÖBB" },
                    { value: "wl", label: "Wiener Linien" },
                  ],
                },
              },
            },
            { name: "rows", selector: { number: { min: 1, max: 20, mode: "box" } } },
            { name: "auto_rotate", selector: { number: { min: 0, max: 600, step: 5, mode: "box", unit_of_measurement: "s" } } },
            { name: "show_platform", selector: { boolean: {} } },
            { name: "show_clock", selector: { boolean: {} } },
          ],
        },
      ],
      computeLabel: (s) =>
        ({
          entities: "Haltestellen / Stops",
          title: "Titel / Title",
          theme: "Design / Theme",
          rows: "Zeilen / Rows",
          auto_rotate: "Automatisch wechseln / Auto rotate",
          show_platform: "Gleis anzeigen / Show platform",
          show_clock: "Uhr anzeigen / Show clock",
        })[s.name],
    };
  }

  setConfig(config) {
    const entities = config.entities || (config.entity ? [config.entity] : []);
    if (!entities.length) throw new Error("Bitte mindestens eine Haltestelle (entities) angeben");
    this._config = {
      theme: "auto",
      rows: 8,
      show_platform: true,
      show_clock: true,
      auto_rotate: 0,
      ...config,
      entities: entities.map((e) => (typeof e === "string" ? e : e.entity)),
    };
    this._index = Math.min(this._index, this._config.entities.length - 1);
    this._lastKey = "";
    this._setupRotation();
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    const st = hass.states[this._entity()];
    const key = st ? st.last_updated + st.state : "x";
    if (key !== this._lastKey) {
      this._lastKey = key;
      this._render();
    }
  }

  getCardSize() {
    return 1 + Math.ceil((this._config?.rows || 8) * 0.7);
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  connectedCallback() {
    this._tick = setInterval(() => this._render(), 15000);
    this._setupRotation();
  }

  disconnectedCallback() {
    clearInterval(this._tick);
    clearInterval(this._rot);
  }

  _setupRotation() {
    clearInterval(this._rot);
    const sec = Number(this._config?.auto_rotate || 0);
    if (sec > 0 && this._config.entities.length > 1 && this.isConnected) {
      this._rot = setInterval(() => {
        if (!this._formation) this._switch(1);
      }, Math.max(5, sec) * 1000);
    }
  }

  _t(k) {
    const lang = (this._hass?.locale?.language || this._hass?.language || "de").slice(0, 2);
    return (TEXT[lang] || TEXT.en)[k];
  }

  _entity() {
    return this._config.entities[this._index] || this._config.entities[0];
  }

  _switch(step) {
    const n = this._config.entities.length;
    this._index = (this._index + step + n) % n;
    this._render();
  }

  _theme(stateObj) {
    let name = this._config.theme;
    if (name === "auto" || !THEMES[name]) {
      const p = stateObj?.attributes?.provider;
      name = p === "wiener_linien" ? "wl" : p === "scotty" ? "oebb" : "standard";
    }
    return THEMES[name] || THEMES.standard;
  }

  _render() {
    if (!this._config || !this._hass) return;
    const stateObj = this._hass.states[this._entity()];
    const th = this._theme(stateObj);
    const a = stateObj?.attributes || {};
    const now = Date.now();
    const name = this._config.title || this._friendlyStop(stateObj) || a.station || this._entity();
    let clock;
    try {
      clock = new Date().toLocaleTimeString("de-AT", { hour: "2-digit", minute: "2-digit", timeZone: this._hass.config?.time_zone || undefined });
    } catch (e) {
      clock = new Date().toLocaleTimeString("de-AT", { hour: "2-digit", minute: "2-digit" });
    }

    let body = "";
    if (!stateObj) {
      body = `<div class="msg err">${this._t("missing")}: ${esc(this._entity())}</div>`;
    } else if (stateObj.state === "unavailable") {
      body = `<div class="msg err">${this._t("unavailable")}</div>`;
    } else {
      const deps = (a.departures || [])
        .filter((d) => !d.departure || new Date(d.departure).getTime() > now - 30000)
        .slice(0, this._config.rows);
      body = deps.length
        ? deps.map((d, i) => this._row(d, i, now)).join("")
        : `<div class="msg">${this._t("none")}</div>`;
    }

    const dots =
      this._config.entities.length > 1
        ? `<div class="dots">${this._config.entities.map((_, i) => `<span class="${i === this._index ? "on" : ""}"></span>`).join("")}</div>`
        : "";

    this.shadowRoot.innerHTML = `
      <style>${this._css(th)}</style>
      <ha-card>
        <div class="header" id="header" title="${esc(this._entity())}">
          <div class="name">${esc(name)}</div>
          ${dots}
          ${this._config.show_clock ? `<div class="clock">${clock}</div>` : ""}
        </div>
        <div class="rows">${body}</div>
        ${a.attribution ? `<div class="attr">${esc(a.attribution)}</div>` : ""}
        ${this._formation ? this._formationView(th) : ""}
      </ha-card>`;

    this.shadowRoot.getElementById("header").addEventListener("click", () => {
      if (this._config.entities.length > 1) this._switch(1);
      else this._moreInfo();
    });
    this.shadowRoot.querySelectorAll(".row[data-train]").forEach((el) =>
      el.addEventListener("click", () => this._openFormation(el.dataset))
    );
    const close = this.shadowRoot.getElementById("close");
    if (close) close.addEventListener("click", () => {
      this._formation = null;
      this._render();
    });
  }

  _friendlyStop(stateObj) {
    // Gerätename = Name der Haltestelle; der Sensor heißt "<Gerät> Nächste Abfahrt"
    const fn = stateObj?.attributes?.friendly_name || "";
    const dev = this._hass.entities?.[this._entity()]?.device_id;
    const devName = dev && this._hass.devices?.[dev] ? this._hass.devices[dev].name_by_user || this._hass.devices[dev].name : "";
    return devName || fn;
  }

  _moreInfo() {
    const ev = new Event("hass-more-info", { bubbles: true, composed: true });
    ev.detail = { entityId: this._entity() };
    this.dispatchEvent(ev);
  }

  _row(d, i, now) {
    const when = d.departure ? new Date(d.departure).getTime() : null;
    const mins = when != null ? Math.floor((when - now) / 60000) : null;
    let countdown = "";
    if (d.cancelled) countdown = `<span class="red">${this._t("cancelled")}</span>`;
    else if (mins != null && mins <= 0) countdown = `<span class="acc">${this._t("now")}</span>`;
    else if (mins != null && mins < 60) countdown = `<span class="${mins <= 2 ? "acc" : ""}">${mins}'</span>`;

    let delay = "";
    if (d.cancelled) delay = `<span class="red">✕</span>`;
    else if (d.delay > 0) delay = `<span class="red">+${d.delay}</span>`;
    else if (d.delay === 0) delay = `<span class="ok"></span>`;

    const destCls = d.cancelled ? "red" : d.destination_changed ? "yellow" : "";
    const clickable = d.train_number && this._hass.states[this._entity()]?.attributes?.provider === "scotty";
    const data = clickable
      ? `data-train="${d.train_number}" data-date="${esc(d.date)}" data-line="${esc(d.line)}"`
      : "";
    return `
      <div class="row ${i % 2 ? "alt" : ""} ${clickable ? "click" : ""}" ${data}>
        <div class="time ${d.cancelled ? "dim" : ""}">${esc(d.time)}</div>
        <div class="delay">${delay}</div>
        <div class="badge" style="background:${badgeColor(d)}">${esc(badgeText(d))}</div>
        <div class="dest ${destCls}">${esc(d.destination)}</div>
        ${this._config.show_platform ? `<div class="plat">${esc(d.platform)}</div>` : ""}
        <div class="cd">${countdown}</div>
      </div>`;
  }

  async _openFormation(ds) {
    const station = this._hass.states[this._entity()]?.attributes?.station || "";
    this._formation = { loading: true, line: ds.line };
    this._render();
    try {
      const res = await this._hass.callService(
        "grad_no",
        "get_formation",
        { train_number: Number(ds.train), station, date: ds.date || undefined },
        undefined,
        false,
        true
      );
      this._formation = { ...res.response, line: ds.line };
    } catch (err) {
      this._formation = { error: err?.message || String(err), line: ds.line };
    }
    this._render();
  }

  _formationView(th) {
    const f = this._formation;
    let inner;
    if (f.loading) inner = `<div class="msg">${this._t("loading")}</div>`;
    else if (f.error) inner = `<div class="msg err">${esc(f.error)}</div>`;
    else {
      const multi = (f.parts || []).length > 1;
      const wagons = (f.wagons || [])
        .map((w) => {
          const icons = [];
          if (w.dining) icons.push(`<ha-icon icon="mdi:silverware-fork-knife" title="${this._t("dining")}"></ha-icon>`);
          if (w.bikes) icons.push(`<ha-icon icon="mdi:bicycle" title="${this._t("bikes")}"></ha-icon>`);
          if (w.wheelchairs) icons.push(`<ha-icon icon="mdi:wheelchair" title="${this._t("wheelchair")}"></ha-icon>`);
          if (w.quiet) icons.push(`<ha-icon icon="mdi:volume-off" title="${this._t("quiet")}"></ha-icon>`);
          if (w.family) icons.push(`<ha-icon icon="mdi:human-male-child" title="${this._t("family")}"></ha-icon>`);
          const cls = w.first > 0 || w.business > 0;
          const load =
            f.load_info && w.load
              ? `<div class="load l${w.load}" title="${this._t("load")}">${[1, 2, 3].map((k) => `<i class="${k <= w.load ? "on" : ""}"></i>`).join("")}</div>`
              : `<div class="load"></div>`;
          return `
            <div class="wagon ${w.kind} ${w.dining ? "dining" : ""} part${w.part}">
              <div class="nr">${w.number ?? ""}</div>
              <div class="box">${cls ? `<div class="cls" title="${w.business ? this._t("business") : this._t("first")}"></div>` : ""}
                <div class="icons">${icons.join("")}</div></div>
              ${load}
            </div>`;
        })
        .join("");
      const parts = multi
        ? `<div class="parts">${f.parts.map((p, i) => `<span class="part${i}">${this._t("to")} ${esc(p)}</span>`).join("")}</div>`
        : "";
      inner = `
        <div class="ftitle">${esc(f.title || f.line)}</div>
        <div class="fsub">${esc(f.station)}${f.platform ? ` · ${this._t("platform")} ${esc(f.platform)}` : ""}</div>
        ${parts}
        <div class="train">${wagons}</div>`;
    }
    return `
      <div class="overlay">
        <div class="fhead"><span>${this._t("formation")} ${esc(f.line || "")}</span>
          <button id="close" aria-label="${this._t("close")}">✕</button></div>
        ${inner}
      </div>`;
  }

  _css(th) {
    return `
      :host { display:block; }
      ha-card { display:block; background:${th.bg}; color:${th.text}; overflow:hidden; position:relative;
        font-family: "Roboto Condensed", "Arial Narrow", Roboto, system-ui, sans-serif; border:none; }
      .header { display:flex; align-items:center; gap:10px; background:${th.header}; padding:10px 14px;
        border-bottom:2px solid ${th.accent}; cursor:pointer; }
      .name { flex:1; font-size:1.35em; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
      .clock { font-size:1.35em; font-weight:600; color:${th.accent}; font-variant-numeric:tabular-nums; }
      .dots { display:flex; gap:5px; }
      .dots span { width:7px; height:7px; border-radius:50%; border:1px solid ${th.sub}; }
      .dots span.on { background:${th.accent}; border-color:${th.accent}; }
      .rows { padding:2px 0; }
      .row { display:grid; grid-template-columns: 3.1em 2.2em 4.8em 1fr auto 3.6em; align-items:center;
        gap:6px; padding:5px 12px; font-size:1.05em; min-height:1.8em; }
      .row.alt { background:${th.alt}; }
      .row.click { cursor:pointer; }
      .row.click:hover { filter:brightness(1.25); }
      .time { color:${th.accent}; font-variant-numeric:tabular-nums; }
      .time.dim { color:${th.sub}; text-decoration:line-through; }
      .delay { font-size:.9em; }
      .ok { display:inline-block; width:6px; height:6px; border-radius:50%; background:${GREEN}; vertical-align:middle; margin-left:4px; }
      .badge { color:#fff; border-radius:4px; text-align:center; font-size:.85em; font-weight:600; padding:2px 3px;
        white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
      .dest { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
      .plat { color:${th.sub}; font-size:.9em; text-align:right; min-width:1.6em; }
      .cd { text-align:right; font-variant-numeric:tabular-nums; }
      .red { color:${RED}; } .yellow { color:${YELLOW}; } .acc { color:${th.accent}; }
      .msg { padding:24px 14px; text-align:center; color:${th.sub}; }
      .msg.err { color:${RED}; }
      .attr { font-size:.65em; color:${th.sub}; opacity:.7; padding:2px 12px 6px; text-align:right; }
      .overlay { position:absolute; inset:0; background:${th.bg}; display:flex; flex-direction:column; padding:0 0 10px; overflow:auto; }
      .fhead { display:flex; justify-content:space-between; align-items:center; background:${th.header};
        border-bottom:2px solid ${th.accent}; padding:10px 14px; font-size:1.2em; font-weight:600; }
      .fhead button { background:none; border:none; color:${th.text}; font-size:1.1em; cursor:pointer; }
      .ftitle { padding:10px 14px 0; font-size:1.15em; font-weight:600; }
      .fsub { padding:2px 14px 6px; color:${th.sub}; }
      .parts { display:flex; gap:12px; padding:0 14px 6px; font-size:.9em; }
      .parts .part1, .train .part1 .box { outline-color:${th.accent}; }
      .parts span { border-bottom:3px solid ${th.sub}; } .parts .part1 { border-color:${th.accent}; }
      .train { display:flex; flex-wrap:wrap; gap:8px 3px; padding:6px 14px 4px; align-items:flex-end; }
      .wagon { display:flex; flex-direction:column; align-items:center; gap:3px; flex:0 0 auto; }
      .nr { font-size:.8em; color:${th.sub}; min-height:1.1em; }
      .box { width:48px; height:34px; background:#e1e4eb; border-radius:4px; position:relative; color:#0059aa;
        display:flex; align-items:center; justify-content:center; }
      .part1 .box { box-shadow: inset 0 -3px 0 ${th.accent}; }
      .wagon.cab .box { border-radius:14px 4px 4px 4px; }
      .wagon.engine .box { background:#96192a; border-radius:10px 10px 4px 4px; width:40px; }
      .wagon.dining .box { background:#824628; color:#fff; }
      .cls { position:absolute; top:0; left:0; right:0; height:5px; background:${YELLOW}; border-radius:4px 4px 0 0; }
      .icons { display:flex; gap:1px; --mdc-icon-size:14px; }
      .load { display:flex; gap:2px; min-height:6px; }
      .load i { width:6px; height:6px; border:1px solid ${th.sub}; display:block; }
      .load.l1 i.on { background:${GREEN}; border-color:${GREEN}; }
      .load.l2 i.on { background:${YELLOW}; border-color:${YELLOW}; }
      .load.l3 i.on { background:${RED}; border-color:${RED}; }
      @media (max-width: 420px) {
        .row { grid-template-columns: 2.9em 1.9em 3.6em 1fr auto 2.9em; gap:4px; padding:5px 8px; font-size:.95em; }
      }`;
  }
}

// Das Home-Assistant-Frontend tauscht beim Start die Element-Registry aus (scoped custom elements).
// Wird die Karte vorher geladen, ist sie danach unbekannt -> in den ersten Sekunden erneut registrieren.
function registerGradNoCard() {
  if (!customElements.get("grad-no-card")) customElements.define("grad-no-card", class extends GradNoCard {});
}
registerGradNoCard();
let gradNoTries = 0;
const gradNoTimer = setInterval(() => {
  registerGradNoCard();
  if (++gradNoTries > 120) clearInterval(gradNoTimer);
}, 250);

if (!window.__gradNoCard) {
  window.__gradNoCard = true;
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "grad-no-card",
    name: "Grad no! Abfahrtstafel",
    description: "Abfahrten von ÖBB und Wiener Linien im Look der Grad-no!-Anzeige",
    preview: true,
    documentationURL: "https://github.com/emils1988/grad-no-homeassistant",
  });
  console.info(`%c GRAD NO! %c ${CARD_VERSION} `, "background:#ffb000;color:#000;font-weight:700", "background:#1b2a80;color:#fff");
}
