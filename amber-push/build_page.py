"""Build amber-prices.html: a static page charting the Amber prices for each site.

Fetches the same intervals as push_amber.py (60 previous, 288 next, 5-minute resolution) and writes
them into one self-contained HTML file. Tokens are not written to the page.

Run:

    python build_page.py    # then open amber-prices.html in a browser
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import push_amber as p

OUT = Path(os.getenv("OUTPUT_FILE") or Path(__file__).with_name("amber-prices.html"))
SECTIONS = ["ryde", "epping"]
PREVIOUS = 60
NEXT = 432
RESOLUTION = 5
TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Amber Forecast</title>
<style>
  :root {
    --bg: #0b0d12;
    --panel: #14171f;
    --border: #262b36;
    --text: #d6d9e0;
    --muted: #8a909c;
    --grid: #262b36;
    --actual: #73bf69;
    --current: #fade2a;
    --forecast: #5794f2;
    --best: rgba(250, 222, 42, 0.16);
  }
  @media (prefers-color-scheme: light) {
    :root:not([data-theme="dark"]) {
      --bg: #f4f5f7;
      --panel: #ffffff;
      --border: #d9dde5;
      --text: #22252b;
      --muted: #5f6672;
      --grid: #e4e7ed;
    }
  }
  :root[data-theme="light"] {
    --bg: #f4f5f7;
    --panel: #ffffff;
    --border: #d9dde5;
    --text: #22252b;
    --muted: #5f6672;
    --grid: #e4e7ed;
  }
  body {
    margin: 0;
    padding: 16px;
    background: var(--bg);
    color: var(--text);
    font: 14px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .meta { color: var(--muted); margin-bottom: 20px; }
  .site { margin-bottom: 28px; }
  .site h2 { font-size: 16px; margin: 0 0 10px; font-weight: 600; }
  .site h2 span { color: var(--muted); font-weight: 400; font-size: 13px; margin-left: 8px; }
  .grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(min(100%, 460px), 1fr)); }
  .panel { background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 12px 14px; min-width: 0; }
  .panel h3 { font-size: 15px; margin: 0 0 6px; font-weight: 600; }
  .panel svg { display: block; width: 100%; height: auto; }
  .legend { display: flex; flex-wrap: wrap; gap: 14px; margin-top: 6px; color: var(--muted); font-size: 12px; }
  .legend i { display: inline-block; width: 14px; height: 3px; border-radius: 2px; margin-right: 6px; vertical-align: middle; }
  .axis { fill: var(--muted); font-size: 12px; }
  .gridline { stroke: var(--grid); stroke-width: 1; }
  .zero { stroke: var(--muted); stroke-width: 1; }
  .now { stroke: var(--text); stroke-width: 1; stroke-dasharray: 4 4; opacity: 0.6; }
  .bar:hover { stroke-width: 2.4; }
  .best-band { fill: var(--best); }
  .best-swatch {
    display: inline-block;
    width: 14px;
    height: 8px;
    margin-right: 6px;
    vertical-align: middle;
    background: var(--best);
    border: 1px solid var(--current);
    border-radius: 2px;
  }
  .tip {
    position: fixed;
    z-index: 10;
    pointer-events: none;
    max-width: 260px;
    padding: 8px 10px;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 6px;
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.35);
    font-size: 12px;
    line-height: 1.5;
  }
  .tip-head { color: var(--muted); }
  .tip-price { font-size: 15px; font-weight: 600; }
  .toggle { display: inline-flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; margin-bottom: 14px; }
  .toggle button { background: var(--panel); color: var(--muted); border: 0; padding: 6px 14px; font: inherit; cursor: pointer; }
  .toggle button[aria-pressed="true"] { background: var(--text); color: var(--bg); }
  .empty { color: var(--muted); padding: 40px 0; text-align: center; }
  @media (max-width: 480px) {
    body { padding: 16px; }
    .panel { padding: 10px; }
  }
</style>
</head>
<body>
<h1>Amber Forecast</h1>
<div class="toggle" id="toggle"></div>
<div class="meta" id="meta"></div>
<div id="sites"></div>
<div class="tip" id="tip" hidden></div>

<script>
const DATA = __DATA__;
const GENERATED = __GENERATED__;

const COLORS = { ActualInterval: "var(--actual)", CurrentInterval: "var(--current)", ForecastInterval: "var(--forecast)" };
const LABELS = { ActualInterval: "Actual", CurrentInterval: "Current", ForecastInterval: "Forecast" };
const CHANNEL_NAMES = { general: "General", controlledLoad: "Controlled Load", feedIn: "Feed-in" };
const CHANNEL_ORDER = ["general", "controlledLoad", "feedIn"];
const NS = "http://www.w3.org/2000/svg";
const W = 720, H = 300, PL = 46, PR = 24, PT = 12, PB = 34;
const TWO_HOURS = 2 * 3600e3;
const HOUR = 3600e3;
const BLOCK = 4 * HOUR;
const VIEWS = {
  overview: { label: "Overview", forecast: Infinity },
  focus: { label: "Focus", forecast: 4 * HOUR },
};
const TZ = "Australia/Sydney";
const fmtHour = new Intl.DateTimeFormat("en-AU", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hour12: false });
const fmtDay = new Intl.DateTimeFormat("en-AU", { timeZone: TZ, weekday: "short", day: "numeric", month: "short" });
const fmtStamp = new Intl.DateTimeFormat("en-AU", { timeZone: TZ, dateStyle: "medium", timeStyle: "short" });

function el(name, attrs = {}, text) {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}

function niceStep(raw) {
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  const f = raw / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
}

function lowestBlock(allPoints) {
  const now = Date.now();
  const future = allPoints.filter(p => p.t1 > now);
  const lastEnd = Math.max(...future.map(p => p.t1));
  let best = null;
  for (const candidate of future) {
    const start = Math.max(candidate.t0, now);
    const end = start + BLOCK;
    if (end > lastEnd) continue;
    let weighted = 0;
    let covered = 0;
    for (const p of future) {
      const overlap = Math.min(p.t1, end) - Math.max(p.t0, start);
      if (overlap > 0) {
        weighted += p.v * overlap;
        covered += overlap;
      }
    }
    if (covered < BLOCK) continue;
    const avg = weighted / BLOCK;
    if (!best || avg < best.avg) best = { start, end, avg };
  }
  if (best) {
    best.label = `${fmtRange.format(new Date(best.start))} – ${fmtHour.format(new Date(best.end))}, avg ${best.avg.toFixed(2)} c/kWh`;
  }
  return best;
}

function drawPanel(allPoints, view, best) {
  const now = Date.now();
  const points = allPoints.filter(p => p.t1 > now - HOUR && p.t0 < now + VIEWS[view].forecast);
  const x0 = Math.min(...points.map(p => p.t0));
  const x1 = Math.max(...points.map(p => p.t1));
  const vmin = Math.min(0, ...points.map(p => p.v));
  const vmax = Math.max(0, ...points.map(p => p.v));
  const step = niceStep(Math.max(vmax - vmin, 1) / 5);
  const lo = Math.floor(vmin / step) * step;
  const hi = Math.ceil(vmax / step) * step;
  const decimals = step < 1 ? 1 : 0;

  const X = t => PL + (t - x0) / (x1 - x0) * (W - PL - PR);
  const Y = v => PT + (hi - v) / (hi - lo) * (H - PT - PB);
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });

  for (let v = lo; v <= hi + 1e-9; v += step) {
    svg.append(el("line", { class: "gridline", x1: PL, x2: W - PR, y1: Y(v), y2: Y(v) }));
    svg.append(el("text", { class: "axis", x: PL - 6, y: Y(v) + 4, "text-anchor": "end" }, v.toFixed(decimals)));
  }
  svg.append(el("line", { class: "zero", x1: PL, x2: W - PR, y1: Y(0), y2: Y(0) }));

  if (best) {
    const bx0 = Math.max(X(best.start), PL);
    const bx1 = Math.min(X(best.end), W - PR);
    if (bx1 > bx0) {
      svg.append(el("rect", { class: "best-band", x: bx0, y: PT, width: bx1 - bx0, height: H - PT - PB }));
    }
  }

  for (let t = Math.ceil(x0 / TWO_HOURS) * TWO_HOURS; t <= x1; t += TWO_HOURS) {
    const d = new Date(t);
    const midnight = fmtHour.format(d) === "00:00";
    svg.append(el("line", { class: "gridline", x1: X(t), x2: X(t), y1: PT, y2: H - PB }));
    svg.append(el("text", { class: "axis", x: X(t), y: H - PB + 16, "text-anchor": "middle" }, fmtHour.format(d)));
    if (midnight) svg.append(el("text", { class: "axis", x: X(t), y: H - PB + 30, "text-anchor": "middle" }, fmtDay.format(d)));
  }

  if (now >= x0 && now <= x1) {
    svg.append(el("line", { class: "now", x1: X(now), x2: X(now), y1: PT, y2: H - PB }));
  }

  for (const p of points) {
    const xa = X(p.t0), xb = X(p.t1);
    const ya = Y(Math.max(p.v, 0)), yb = Y(Math.min(p.v, 0));
    const rect = el("rect", {
      class: "bar",
      x: xa, y: ya,
      width: Math.max(1, xb - xa - 1),
      height: Math.max(1, yb - ya),
      fill: "none",
      style: `stroke: ${COLORS[p.type]}`,
      "stroke-width": 1.2,
      "data-t0": p.t0,
      "data-t1": p.t1,
      "data-v": p.v,
      "data-spot": p.spot,
      "data-ren": p.renewables ?? "",
      "data-type": p.type,
    });
    svg.append(rect);
  }
  return svg;
}

function legend(best) {
  const items = Object.keys(LABELS).map(type =>
    `<span><i style="background:${COLORS[type]}"></i>${LABELS[type]}</span>`);
  if (best) items.push(`<span><i class="best-swatch"></i>Lowest 4h: ${best.label}</span>`);
  return `<div class="legend">${items.join("")}</div>`;
}

function render(view) {
  document.getElementById("meta").textContent =
    `Prices in c/kWh, shown in Sydney time. Data fetched ${fmtStamp.format(new Date(GENERATED))}.`;
  for (const button of document.querySelectorAll("#toggle button")) {
    button.setAttribute("aria-pressed", String(button.dataset.view === view));
  }
  const root = document.getElementById("sites");
  root.replaceChildren();
  for (const site of DATA) {
    const section = document.createElement("section");
    section.className = "site";
    section.innerHTML = `<h2>${site.name}<span>${site.site}</span></h2><div class="grid"></div>`;
    const grid = section.querySelector(".grid");
    const channels = Object.keys(site.channels).sort((a, b) => CHANNEL_ORDER.indexOf(a) - CHANNEL_ORDER.indexOf(b));
    for (const channel of channels) {
      const panel = document.createElement("div");
      panel.className = "panel";
      panel.innerHTML = `<h3>${site.name} - ${CHANNEL_NAMES[channel] || channel}</h3>`;
      const best = channel === "feedIn" ? null : lowestBlock(site.channels[channel]);
      panel.append(drawPanel(site.channels[channel], view, best));
      panel.insertAdjacentHTML("beforeend", legend(best));
      grid.append(panel);
    }
    root.append(section);
  }
}

function viewFromHash() {
  return location.hash === "#focus" ? "focus" : "overview";
}

const toggle = document.getElementById("toggle");
for (const [view, config] of Object.entries(VIEWS)) {
  const button = document.createElement("button");
  button.type = "button";
  button.dataset.view = view;
  button.textContent = config.label;
  toggle.append(button);
}
toggle.addEventListener("click", event => {
  const view = event.target.dataset.view;
  if (!view) return;
  history.replaceState(null, "", view === "focus" ? "#focus" : "#overview");
  render(view);
});

const tip = document.getElementById("tip");
const sites = document.getElementById("sites");
const fmtRange = new Intl.DateTimeFormat("en-AU", {
  timeZone: TZ, weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false,
});

sites.addEventListener("pointermove", event => {
  const bar = event.target.closest("rect.bar");
  if (!bar) {
    tip.hidden = true;
    return;
  }
  const d = bar.dataset;
  const channel = bar.closest(".panel").querySelector("h3").textContent;
  const lines = [
    `<div class="tip-head">${channel}</div>`,
    `<div>${LABELS[d.type]} · ${fmtRange.format(new Date(Number(d.t0)))} – ${fmtHour.format(new Date(Number(d.t1)))}</div>`,
    `<div class="tip-price">${Number(d.v).toFixed(2)} c/kWh</div>`,
    `<div>Spot ${Number(d.spot).toFixed(2)} c/kWh</div>`,
  ];
  if (d.ren !== "") lines.push(`<div>Renewables ${Number(d.ren).toFixed(0)}%</div>`);
  tip.innerHTML = lines.join("");
  tip.hidden = false;

  const pad = 14;
  let x = event.clientX + pad;
  let y = event.clientY + pad;
  if (x + tip.offsetWidth > window.innerWidth) x = event.clientX - tip.offsetWidth - pad;
  if (y + tip.offsetHeight > window.innerHeight) y = event.clientY - tip.offsetHeight - pad;
  tip.style.left = `${x}px`;
  tip.style.top = `${y}px`;
});
sites.addEventListener("pointerleave", () => {
  tip.hidden = true;
});

render(viewFromHash());
</script>
</body>
</html>
"""


def points(intervals: list[dict]) -> list[dict]:
    """Convert API intervals to chart points with epoch-millisecond start and end times."""
    out = []
    for interval in intervals:
        start = p.start_time(interval)
        end = start + timedelta(minutes=interval["duration"])
        out.append({
            "t0": int(start.timestamp() * 1000),
            "t1": int(end.timestamp() * 1000),
            "v": float(interval["perKwh"]),
            "spot": float(interval["spotPerKwh"]),
            "renewables": float(interval["renewables"]) if "renewables" in interval else None,
            "type": interval["type"],
        })
    return out


def fetch_sites() -> list[dict]:
    """Fetch every configured site and group its intervals by channel."""
    sites = []
    for section in SECTIONS:
        site, token = p.load_amber_section(section)
        intervals = p.fetch_prices(site, token, NEXT, PREVIOUS, RESOLUTION)
        channels: dict[str, list[dict]] = {}
        for interval in intervals:
            channels.setdefault(interval["channelType"], []).append(interval)
        sites.append({
            "name": section.capitalize(),
            "site": site,
            "channels": {channel: points(items) for channel, items in channels.items()},
        })
    return sites


def main() -> None:
    sites = fetch_sites()
    generated = int(datetime.now(timezone.utc).timestamp() * 1000)
    html = (TEMPLATE
            .replace("__DATA__", json.dumps(sites))
            .replace("__GENERATED__", str(generated)))
    tmp = OUT.with_name(OUT.name + ".tmp")  # write then rename, so a reader never sees a partial page
    tmp.write_text(html, encoding="utf-8")
    os.replace(tmp, OUT)
    counts = {s["name"]: {c: len(v) for c, v in s["channels"].items()} for s in sites}
    print(f"Wrote {OUT} with {counts}")


if __name__ == "__main__":
    main()
