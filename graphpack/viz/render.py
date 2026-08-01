"""Render a run as a self-contained page.

One HTML file with everything inlined: no build step, no package manager, no
request to a CDN. It opens from disk, which is what makes it usable as a
screenshot, a recording, or something to hand to somebody without asking them to
install anything first.

The force-directed layout is about eighty lines of Verlet integration. A library
would do it better, but a library is a network request or a build step, and
neither survives being emailed to somebody.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import asdict

from graphpack.agent.trace import Trace
from graphpack.viz.subgraph import Subgraph

#: One colour per node kind, assigned in the order kinds first appear. Chosen to
#: stay distinguishable in both themes and for the common forms of colour
#: blindness — no red/green pair carries meaning on its own.
PALETTE = ["#4c8dff", "#f2a03d", "#3fc8a0", "#c77dff", "#ff7a85", "#8f9aa8"]


def render_page(trace: Trace, subgraph: Subgraph, title: str = "") -> str:
    """Return a complete HTML document for one run."""
    colours = {kind: PALETTE[i % len(PALETTE)] for i, kind in enumerate(subgraph.kinds)}
    payload = {
        "trace": asdict(trace),
        "nodes": [{**n, "colour": colours.get(n["kind"], PALETTE[-1])} for n in subgraph.nodes],
        "edges": subgraph.edges,
        "missing": subgraph.missing,
    }
    fills = {
        "__TITLE__": html.escape(title or f"{trace.pack}: {trace.question}"),
        "__DATA__": _script_json(payload),
    }
    # One pass, not two chained replaces: a question containing the literal
    # "__DATA__" would otherwise have the graph substituted into the heading.
    return re.sub("__TITLE__|__DATA__", lambda m: fills[m.group()], _TEMPLATE)


def _script_json(payload: dict) -> str:
    """JSON safe to embed in a `<script>` block.

    A statute title or a package name is corpus data, and `</script>` inside a
    JSON string ends the block early — the rest of the graph is then parsed as
    HTML and the page is blank. json.dumps does not escape it, so we do. U+2028
    and U+2029 are here for the same reason: legal in JSON, a line break inside
    a JavaScript string literal.
    """
    text = json.dumps(payload, ensure_ascii=False)
    for char, escaped in (
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("&", "\\u0026"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ):
        text = text.replace(char, escaped)
    return text


_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root {
    --bg: #12151a; --panel: #1a1f27; --line: #2a323d;
    --text: #e6eaf0; --dim: #8b96a5; --accent: #4c8dff;
  }
  @media (prefers-color-scheme: light) {
    :root { --bg: #f7f8fa; --panel: #fff; --line: #e2e6ec;
            --text: #1a1f27; --dim: #6b7280; }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font: 14px/1.5 ui-sans-serif, -apple-system, "Segoe UI", system-ui, sans-serif;
    display: grid; grid-template-rows: auto 1fr; height: 100vh;
  }
  header { padding: 14px 20px; border-bottom: 1px solid var(--line); }
  header h1 { margin: 0 0 2px; font-size: 15px; font-weight: 600; }
  header p { margin: 0; color: var(--dim); font-size: 13px; }
  main { display: grid; grid-template-columns: minmax(0,1fr) 340px; min-height: 0; }
  @media (max-width: 820px) { main { grid-template-columns: 1fr; grid-template-rows: 1fr auto; } }
  #stage { position: relative; min-height: 0; }
  canvas { width: 100%; height: 100%; display: block; }
  aside {
    border-left: 1px solid var(--line); background: var(--panel);
    overflow-y: auto; padding: 14px; min-height: 0;
  }
  @media (max-width: 820px) { aside { border-left: 0; border-top: 1px solid var(--line);
    max-height: 45vh; } }
  .step {
    padding: 9px 11px; border: 1px solid var(--line); border-radius: 7px;
    margin-bottom: 7px; cursor: pointer; background: transparent;
    width: 100%; text-align: left; color: inherit; font: inherit;
    transition: border-color .15s, background .15s;
  }
  .step:hover { border-color: var(--accent); }
  .step[aria-current="true"] { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, transparent); }
  .step .name { font-weight: 600; }
  .step .meta { color: var(--dim); font-size: 12px; }
  .step .summary { margin-top: 3px; font-size: 13px; }
  .controls { display: flex; gap: 8px; margin-bottom: 12px; }
  button.action {
    background: var(--accent); color: #fff; border: 0; border-radius: 6px;
    padding: 7px 14px; font: inherit; font-weight: 600; cursor: pointer;
  }
  button.action.ghost { background: transparent; color: var(--text); border: 1px solid var(--line); }
  .answer {
    margin-top: 14px; padding: 11px; border: 1px solid var(--line);
    border-radius: 7px; font-size: 13px;
  }
  .answer h2 { margin: 0 0 6px; font-size: 12px; text-transform: uppercase;
               letter-spacing: .05em; color: var(--dim); }
  .legend { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 12px;
            font-size: 12px; color: var(--dim); }
  .legend span { display: inline-flex; align-items: center; gap: 5px; }
  .swatch { width: 9px; height: 9px; border-radius: 50%; }
  .warn { color: #ff7a85; }
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <p id="subtitle"></p>
</header>
<main>
  <div id="stage"><canvas id="graph"></canvas></div>
  <aside>
    <div class="controls">
      <button class="action" id="play">Replay</button>
      <button class="action ghost" id="all">Show all</button>
    </div>
    <div id="steps"></div>
    <div class="answer"><h2>Answer</h2><div id="answer"></div></div>
    <div class="legend" id="legend"></div>
  </aside>
</main>
<script>
const DATA = __DATA__;

// ---------------------------------------------------------------- layout
// Verlet integration: repulsion between every pair, springs along edges, a
// gentle pull to the centre. Enough for a readable picture at this size, and
// it costs nothing to ship.

// What the question was about: the ids the first step to find any came back
// with — the lookup. Anchoring these loosely keeps the subject in the middle
// instead of flung to an edge, and they are the nodes always worth a label.
// Capped, because a lookup that matched forty things anchors nothing.
const found = DATA.trace.events.find(e => (e.node_ids || []).length);
const subjects = new Set((found && found.node_ids.length <= 4 ? found.node_ids : []));

const nodes = DATA.nodes.map((n, i) => ({
  ...n,
  x: 300 + Math.cos(i * 2.39996) * (18 + i * 5),
  y: 300 + Math.sin(i * 2.39996) * (18 + i * 5),
  vx: 0, vy: 0,
  anchored: subjects.has(n.id),
}));
const byId = new Map(nodes.map(n => [n.id, n]));
const edges = DATA.edges.filter(e => byId.has(e.start) && byId.has(e.end));

function settle(steps) {
  for (let s = 0; s < steps; s++) {
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        let dx = b.x - a.x, dy = b.y - a.y;
        let d2 = dx * dx + dy * dy || 0.01;
        if (d2 > 90000) continue;
        const f = 2600 / d2;
        const d = Math.sqrt(d2);
        const ux = dx / d * f, uy = dy / d * f;
        a.vx -= ux; a.vy -= uy; b.vx += ux; b.vy += uy;
      }
    }
    for (const e of edges) {
      const a = byId.get(e.start), b = byId.get(e.end);
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const f = (d - 90) * 0.014;
      const ux = dx / d * f, uy = dy / d * f;
      a.vx += ux; a.vy += uy; b.vx -= ux; b.vy -= uy;
    }
    for (const n of nodes) {
      n.vx += (300 - n.x) * (n.anchored ? 0.02 : 0.004);
      n.vy += (300 - n.y) * (n.anchored ? 0.02 : 0.004);
      n.vx *= 0.82; n.vy *= 0.82;
      n.x += n.vx; n.y += n.vy;
    }
  }
}
settle(320);

// ----------------------------------------------------------------- drawing
const canvas = document.getElementById("graph");
const ctx = canvas.getContext("2d");
let lit = null;              // {nodes:Set, edges:Set} or null for everything

function bounds() {
  const xs = nodes.map(n => n.x), ys = nodes.map(n => n.y);
  return { minX: Math.min(...xs), maxX: Math.max(...xs),
           minY: Math.min(...ys), maxY: Math.max(...ys) };
}

function draw() {
  const dpr = window.devicePixelRatio || 1;
  const stage = document.getElementById("stage");
  canvas.width = stage.clientWidth * dpr;
  canvas.height = stage.clientHeight * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, stage.clientWidth, stage.clientHeight);
  if (!nodes.length) return;

  const b = bounds(), pad = 46;
  const sx = (stage.clientWidth - pad * 2) / Math.max(1, b.maxX - b.minX);
  const sy = (stage.clientHeight - pad * 2) / Math.max(1, b.maxY - b.minY);
  const k = Math.min(sx, sy, 2.2);
  const ox = pad - b.minX * k + (stage.clientWidth - pad * 2 - (b.maxX - b.minX) * k) / 2;
  const oy = pad - b.minY * k + (stage.clientHeight - pad * 2 - (b.maxY - b.minY) * k) / 2;
  const P = n => [n.x * k + ox, n.y * k + oy];

  const style = getComputedStyle(document.documentElement);
  const lineColour = style.getPropertyValue("--line").trim();
  const dim = style.getPropertyValue("--dim").trim();
  const text = style.getPropertyValue("--text").trim();

  for (const e of edges) {
    const on = !lit || lit.edges.has(e.start + "|" + e.end);
    const [x1, y1] = P(byId.get(e.start)), [x2, y2] = P(byId.get(e.end));
    ctx.strokeStyle = on ? "#4c8dff" : lineColour;
    ctx.globalAlpha = on ? 0.75 : 0.28;
    ctx.lineWidth = on ? 1.7 : 1;
    // Dashed means the run computed this relation rather than read it: two
    // CITES hops through a decision reported as one CO_CITED edge. Drawing it
    // like a stored edge would claim the graph holds something it does not.
    ctx.setLineDash(e.derived ? [5, 4] : []);
    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    ctx.setLineDash([]);
  }
  ctx.globalAlpha = 1;

  for (const n of nodes) {
    const on = !lit || lit.nodes.has(n.id);
    const [x, y] = P(n);
    const r = on ? 7 : 4.5;
    ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fillStyle = n.colour;
    ctx.globalAlpha = on ? 1 : 0.3;
    ctx.fill();
    if (on) {
      ctx.lineWidth = 1.6;
      ctx.strokeStyle = style.getPropertyValue("--bg").trim();
      ctx.stroke();
    }
    // The subject keeps a ring at every step, so the eye can come back to it.
    if (n.anchored) {
      ctx.globalAlpha = 1;
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = "#4c8dff";
      ctx.beginPath(); ctx.arc(x, y, r + 3.5, 0, Math.PI * 2); ctx.stroke();
    }
    // Labels for the lit set when there are few enough to read — but the nodes
    // the question named are always labelled. A sixty-node overview with no
    // text is a picture of a hairball; one word in the middle makes it a
    // picture of urllib3.
    const few = !lit ? nodes.length <= 28 : lit.nodes.size <= 34;
    if (on && (few || n.anchored)) {
      ctx.globalAlpha = 0.95;
      ctx.fillStyle = lit || n.anchored ? text : dim;
      ctx.font = (n.anchored ? "600 12px " : "11px ") + 'ui-sans-serif, system-ui, sans-serif';
      ctx.fillText(String(n.label).slice(0, 26), x + 10, y + 4);
    }
    ctx.globalAlpha = 1;
  }
}

// ------------------------------------------------------------------ steps
const stepsBox = document.getElementById("steps");
DATA.trace.events.forEach((event, index) => {
  const button = document.createElement("button");
  button.className = "step";
  button.type = "button";
  button.innerHTML =
    `<div><span class="name">${event.step}</span> ` +
    `<span class="meta">${event.tool || ""} · ${event.duration_ms}ms</span></div>` +
    `<div class="summary">${escapeHtml(event.summary || "")}</div>`;
  button.addEventListener("click", () => show(index));
  stepsBox.appendChild(button);
});

function show(index) {
  const event = DATA.trace.events[index];
  lit = {
    nodes: new Set(event.node_ids),
    edges: new Set((event.edge_ids || []).map(e => e[0] + "|" + e[2])),
  };
  // A step that names nodes but no edges still wants its edges drawn: light
  // every edge whose ends are both in the step.
  if (!lit.edges.size) {
    for (const e of edges) {
      if (lit.nodes.has(e.start) && lit.nodes.has(e.end)) lit.edges.add(e.start + "|" + e.end);
    }
  }
  [...stepsBox.children].forEach((el, i) =>
    el.setAttribute("aria-current", String(i === index)));
  draw();
}

function showAll() {
  lit = null;
  [...stepsBox.children].forEach(el => el.setAttribute("aria-current", "false"));
  draw();
}

let timer = null;
document.getElementById("play").addEventListener("click", () => {
  clearInterval(timer);
  let i = 0;
  show(0);
  timer = setInterval(() => {
    i += 1;
    if (i >= DATA.trace.events.length) { clearInterval(timer); return; }
    show(i);
  }, 1100);
});
document.getElementById("all").addEventListener("click", () => { clearInterval(timer); showAll(); });

// ------------------------------------------------------------------- chrome
document.getElementById("answer").textContent = DATA.trace.answer || "(no answer)";
const totalMs = DATA.trace.events.reduce((s, e) => s + e.duration_ms, 0);
document.getElementById("subtitle").textContent =
  `${DATA.nodes.length} nodes · ${edges.length} edges · ` +
  `${DATA.trace.events.length} steps · ${totalMs}ms`;

const legend = document.getElementById("legend");
const kinds = [...new Set(DATA.nodes.map(n => n.kind).filter(Boolean))];
for (const kind of kinds) {
  const colour = DATA.nodes.find(n => n.kind === kind).colour;
  const el = document.createElement("span");
  el.innerHTML = `<i class="swatch" style="background:${colour}"></i>${escapeHtml(kind)}`;
  legend.appendChild(el);
}
if (edges.some(e => e.derived)) {
  const note = document.createElement("span");
  note.innerHTML =
    '<i style="width:15px;height:0;border-top:1.5px dashed currentColor"></i>' +
    "derived by the run, not stored in the graph";
  legend.appendChild(note);
}
if (DATA.missing.length) {
  const el = document.createElement("span");
  el.className = "warn";
  el.textContent = `${DATA.missing.length} cited id(s) not in the graph`;
  legend.appendChild(el);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

window.addEventListener("resize", draw);
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", draw);
showAll();
</script>
</body>
</html>
"""
