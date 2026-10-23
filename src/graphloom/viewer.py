"""Self-contained HTML viewer. No CDN, no build step, works from a local file."""

from __future__ import annotations

import html
import json
from pathlib import Path

from graphloom.model import Graph

_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Graphloom — @@GRAPHLOOM_TITLE@@</title>
<style>
  :root {
    --ink: #16130f;
    --panel: #221c16;
    --line: #3a3128;
    --paper: #f3eadb;
    --muted: #b7aa98;
    --clay: #d17a3a;
    --sage: #7f9e7a;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; background: var(--ink); color: var(--paper); }
  body {
    font: 14px/1.45 "Segoe UI", Helvetica, Arial, sans-serif;
    display: flex;
    flex-direction: column;
    min-height: 100%;
  }
  header {
    display: flex;
    flex-wrap: wrap;
    gap: 12px 20px;
    align-items: center;
    padding: 12px 16px;
    border-bottom: 1px solid var(--line);
    background: #1c1712;
  }
  h1 {
    font-family: Palatino, "Palatino Linotype", Georgia, serif;
    font-size: 20px;
    font-weight: 600;
    letter-spacing: 0.12em;
    margin: 0;
  }
  h1 span { color: var(--clay); letter-spacing: 0; }
  .meta { color: var(--muted); font-size: 12px; }
  label.search { flex: 1; min-width: 180px; display: flex; align-items: center; gap: 8px; }
  input[type="search"] {
    flex: 1;
    background: var(--ink);
    color: var(--paper);
    border: 1px solid var(--line);
    border-radius: 999px;
    padding: 8px 12px;
  }
  .filters { display: flex; flex-wrap: wrap; gap: 8px 12px; padding: 8px 16px; border-bottom: 1px solid var(--line); }
  .filters label { color: var(--muted); font-size: 12px; display: flex; gap: 6px; align-items: center; }
  #shell { flex: 1; display: grid; grid-template-columns: 1fr 320px; min-height: 0; }
  #stage { position: relative; min-height: 360px; }
  canvas { width: 100%; height: 100%; display: block; cursor: grab; }
  canvas.dragging { cursor: grabbing; }
  #inspector {
    border-left: 1px solid var(--line);
    background: var(--panel);
    overflow: auto;
    padding: 16px;
  }
  #inspector h2 { font-family: Palatino, Georgia, serif; font-size: 18px; margin: 0 0 8px; }
  #inspector p { margin: 0 0 8px; color: var(--muted); }
  #inspector .id { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; word-break: break-all; color: var(--paper); }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; margin-right: 6px; color: #1a140f; }
  ul.edges { list-style: none; padding: 0; margin: 8px 0 0; }
  ul.edges li { padding: 6px 0; border-top: 1px solid var(--line); font-size: 12px; }
  ul.edges button {
    background: none; border: 0; color: var(--paper); padding: 0; text-align: left; cursor: pointer;
    font: inherit;
  }
  ul.edges button:hover { color: var(--clay); }
  .prov { color: var(--muted); }
  footer { padding: 8px 16px; color: var(--muted); font-size: 12px; border-top: 1px solid var(--line); }
  @media (max-width: 800px) {
    #shell { grid-template-columns: 1fr; grid-template-rows: minmax(320px, 55vh) auto; }
    #inspector { border-left: 0; border-top: 1px solid var(--line); max-height: none; }
  }
</style>
</head>
<body>
<header>
  <h1>GRAPH<span>LOOM</span></h1>
  <div class="meta" id="stats"></div>
  <label class="search">Search
    <input id="q" type="search" placeholder="function, table, file, node id" autocomplete="off">
  </label>
</header>
<div class="filters" id="filters"></div>
<div id="shell">
  <div id="stage"><canvas id="loom"></canvas></div>
  <aside id="inspector"></aside>
</div>
<footer>Offline viewer. Threads are edges; beads are nodes. Nothing here calls the network.</footer>
<script id="loom-data" type="application/json">@@GRAPHLOOM_DATA@@</script>
<script>
(function () {
  const payload = JSON.parse(document.getElementById("loom-data").textContent);
  const LAYER = { code: "#7f9e7a", data: "#d17a3a", docs: "#d6c08a", config: "#6f8f9a", concept: "#c46b8a" };
  const PROV = { parsed: "rgba(231,215,193,0.8)", linked: "rgba(224,159,62,0.95)", llm: "rgba(211,107,140,0.95)" };
  const layersOn = {};
  const provOn = { parsed: true, linked: true, llm: true };
  Object.keys(LAYER).forEach(function (key) { layersOn[key] = true; });

  const degree = {};
  payload.edges.forEach(function (edge) {
    degree[edge.source] = (degree[edge.source] || 0) + 1;
    degree[edge.target] = (degree[edge.target] || 0) + 1;
  });
  const nodes = payload.nodes.map(function (node, index) {
    const angle = index * 2.399963;
    const radius = 48 + (index % 14) * 16;
    return {
      raw: node,
      id: node.id,
      x: Math.cos(angle) * radius,
      y: Math.sin(angle) * radius,
      vx: 0,
      vy: 0,
      degree: degree[node.id] || 0
    };
  });
  const byId = {};
  nodes.forEach(function (node) { byId[node.id] = node; });
  const edges = payload.edges.filter(function (edge) {
    return byId[edge.source] && byId[edge.target];
  });

  const canvas = document.getElementById("loom");
  const ctx = canvas.getContext("2d");
  const q = document.getElementById("q");
  const inspector = document.getElementById("inspector");
  const stats = document.getElementById("stats");
  stats.textContent = (payload.counts.nodes || nodes.length) + " nodes · " +
    (payload.counts.edges || edges.length) + " edges · " + (payload.root || "");

  const filterBox = document.getElementById("filters");
  function addToggle(group, key, label) {
    const wrap = document.createElement("label");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = true;
    box.addEventListener("change", function () {
      group[key] = box.checked;
      draw();
    });
    wrap.appendChild(box);
    wrap.appendChild(document.createTextNode(" " + label));
    filterBox.appendChild(wrap);
  }
  Object.keys(LAYER).forEach(function (key) { addToggle(layersOn, key, key); });
  Object.keys(PROV).forEach(function (key) { addToggle(provOn, key, key); });

  let selected = null;
  let dragging = null;
  let view = { scale: 1, ox: 0, oy: 0 };
  let query = "";

  function step() {
    const n = nodes.length;
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let dx = nodes[j].x - nodes[i].x;
        let dy = nodes[j].y - nodes[i].y;
        let dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
        let force = 280 / (dist * dist);
        let fx = (dx / dist) * force;
        let fy = (dy / dist) * force;
        nodes[i].vx -= fx;
        nodes[i].vy -= fy;
        nodes[j].vx += fx;
        nodes[j].vy += fy;
      }
    }
    edges.forEach(function (edge) {
      const a = byId[edge.source];
      const b = byId[edge.target];
      let dx = b.x - a.x;
      let dy = b.y - a.y;
      let dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
      let delta = dist - 78;
      let fx = (dx / dist) * delta * 0.05;
      let fy = (dy / dist) * delta * 0.05;
      if (a !== dragging) { a.vx += fx; a.vy += fy; }
      if (b !== dragging) { b.vx += fx; b.vy += fy; }
    });
    nodes.forEach(function (node) {
      if (node === dragging) { node.vx = 0; node.vy = 0; return; }
      node.vx += -node.x * 0.01;
      node.vy += -node.y * 0.01;
      node.vx *= 0.72;
      node.vy *= 0.72;
      node.x += node.vx;
      node.y += node.vy;
    });
  }
  for (let tick = 0; tick < 280; tick++) step();

  function resize() {
    const rect = canvas.parentElement.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(320, rect.width) * dpr;
    canvas.height = Math.max(320, rect.height) * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    fit();
    draw();
  }
  function fit() {
    if (!nodes.length) {
      view = { scale: 1, ox: canvas.clientWidth / 2, oy: canvas.clientHeight / 2 };
      return;
    }
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    nodes.forEach(function (node) {
      minX = Math.min(minX, node.x); maxX = Math.max(maxX, node.x);
      minY = Math.min(minY, node.y); maxY = Math.max(maxY, node.y);
    });
    const w = canvas.clientWidth || 800;
    const h = canvas.clientHeight || 600;
    const scale = Math.min((w - 80) / ((maxX - minX) || 1), (h - 80) / ((maxY - minY) || 1));
    view.scale = Math.max(0.2, Math.min(scale, 2.4));
    view.ox = (w - (minX + maxX) * view.scale) / 2;
    view.oy = (h - (minY + maxY) * view.scale) / 2;
  }
  function screenOf(node) {
    return [view.ox + node.x * view.scale, view.oy + node.y * view.scale];
  }
  function worldOf(x, y) {
    return [(x - view.ox) / view.scale, (y - view.oy) / view.scale];
  }
  function matches(node) {
    if (!query) return true;
    const blob = (node.raw.name + " " + node.id + " " + (node.raw.file || "") + " " + node.raw.kind).toLowerCase();
    return blob.indexOf(query) !== -1;
  }
  function visible(node) {
    const layer = node.raw.layer || "code";
    if (layersOn[layer] === false) return false;
    return matches(node);
  }
  function draw() {
    const w = canvas.clientWidth || 800;
    const h = canvas.clientHeight || 600;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#16130f";
    ctx.fillRect(0, 0, w, h);
    edges.forEach(function (edge) {
      if (provOn[edge.provenance] === false) return;
      const a = byId[edge.source];
      const b = byId[edge.target];
      if (!visible(a) || !visible(b)) return;
      const pa = screenOf(a);
      const pb = screenOf(b);
      ctx.beginPath();
      ctx.strokeStyle = PROV[edge.provenance] || "#eee";
      ctx.lineWidth = edge.relation === "contains" ? 0.6 : 1.3;
      if (edge.provenance === "linked") ctx.setLineDash([5, 4]);
      else if (edge.provenance === "llm") ctx.setLineDash([1, 3]);
      else ctx.setLineDash([]);
      ctx.moveTo(pa[0], pa[1]);
      ctx.lineTo(pb[0], pb[1]);
      ctx.stroke();
      ctx.setLineDash([]);
    });
    nodes.forEach(function (node) {
      if ((node.raw.layer && layersOn[node.raw.layer] === false)) return;
      const dim = query && !matches(node);
      const point = screenOf(node);
      const radius = 5 + Math.min(node.degree, 8);
      ctx.beginPath();
      ctx.fillStyle = LAYER[node.raw.layer] || "#ccc";
      ctx.globalAlpha = dim ? 0.12 : 1;
      ctx.arc(point[0], point[1], radius, 0, Math.PI * 2);
      ctx.fill();
      if (node === selected) {
        ctx.lineWidth = 2;
        ctx.strokeStyle = "#f3eadb";
        ctx.stroke();
      }
      if (!dim) {
        ctx.fillStyle = "#f3eadb";
        ctx.font = "11px Palatino, Georgia, serif";
        ctx.fillText(node.raw.name, point[0] + radius + 3, point[1] + 3);
      }
      ctx.globalAlpha = 1;
    });
  }
  function hit(x, y) {
    let found = null;
    let best = 18;
    nodes.forEach(function (node) {
      if (!visible(node)) return;
      const point = screenOf(node);
      const dx = point[0] - x;
      const dy = point[1] - y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < best) { best = dist; found = node; }
    });
    return found;
  }
  function show(node) {
    inspector.textContent = "";
    if (!node) {
      const p = document.createElement("p");
      p.textContent = nodes.length ? "Click a bead to read its edges and evidence." : "This graph has no nodes yet.";
      inspector.appendChild(p);
      return;
    }
    const raw = node.raw;
    const title = document.createElement("h2");
    title.textContent = raw.name;
    inspector.appendChild(title);
    const id = document.createElement("div");
    id.className = "id";
    id.textContent = raw.id;
    inspector.appendChild(id);
    const badges = document.createElement("p");
    const kind = document.createElement("span");
    kind.className = "badge";
    kind.style.background = LAYER[raw.layer] || "#ccc";
    kind.textContent = (raw.layer || "") + " · " + raw.kind;
    badges.appendChild(kind);
    inspector.appendChild(badges);
    const at = document.createElement("p");
    at.textContent = (raw.file || "") + ":" + raw.line + (raw.language ? " · " + raw.language : "");
    inspector.appendChild(at);
    if (raw.detail) {
      Object.keys(raw.detail).forEach(function (key) {
        const value = raw.detail[key];
        if (value === null || value === "" || key === "role") return;
        const line = document.createElement("p");
        const rendered = Array.isArray(value) ? value.join(", ") : String(value);
        line.textContent = key + ": " + rendered;
        inspector.appendChild(line);
      });
    }
    const list = document.createElement("ul");
    list.className = "edges";
    edges.forEach(function (edge) {
      if (edge.source !== node.id && edge.target !== node.id) return;
      const otherId = edge.source === node.id ? edge.target : edge.source;
      const other = byId[otherId];
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      const arrow = edge.source === node.id ? "→" : "←";
      button.textContent = arrow + " " + edge.relation + " " + (other ? other.raw.name : otherId);
      button.addEventListener("click", function () {
        selected = other || null;
        if (other) show(other);
        draw();
      });
      item.appendChild(button);
      const meta = document.createElement("div");
      meta.className = "prov";
      const evidence = (edge.evidence && edge.evidence[0]) || null;
      meta.textContent = edge.provenance + (evidence ? " · " + evidence.file + ":" + evidence.line + " " + (evidence.excerpt || "") : "");
      item.appendChild(meta);
      list.appendChild(item);
    });
    inspector.appendChild(list);
  }

  q.addEventListener("input", function () {
    query = q.value.trim().toLowerCase();
    draw();
  });
  canvas.addEventListener("mousedown", function (event) {
    const rect = canvas.getBoundingClientRect();
    const node = hit(event.clientX - rect.left, event.clientY - rect.top);
    selected = node;
    dragging = node;
    if (node) canvas.classList.add("dragging");
    show(node);
    draw();
  });
  window.addEventListener("mousemove", function (event) {
    if (!dragging) return;
    const rect = canvas.getBoundingClientRect();
    const world = worldOf(event.clientX - rect.left, event.clientY - rect.top);
    dragging.x = world[0];
    dragging.y = world[1];
    draw();
  });
  window.addEventListener("mouseup", function () {
    dragging = null;
    canvas.classList.remove("dragging");
  });
  window.addEventListener("resize", resize);
  document.addEventListener("keydown", function (event) {
    if (event.key === "/" && document.activeElement !== q) {
      event.preventDefault();
      q.focus();
    }
  });
  show(null);
  resize();
})();
</script>
</body>
</html>
"""


def render_html(graph: Graph) -> str:
    root = graph.meta.get("root") or ""
    title = Path(str(root)).name or "graph"
    payload = {
        "root": root,
        "counts": graph.to_dict()["counts"],
        "nodes": [node.to_dict() for node in graph.nodes.values()],
        "edges": [edge.to_dict() for edge in graph.edges.values()],
    }
    data = json.dumps(payload, ensure_ascii=True).replace("<", "\\u003c")
    page = _TEMPLATE.replace("@@GRAPHLOOM_TITLE@@", html.escape(title))
    page = page.replace("@@GRAPHLOOM_DATA@@", data)
    return page
