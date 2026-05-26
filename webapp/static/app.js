// treeoflife frontend — vanilla JS + D3 v7.
// Pick a taxon → optional CPC subclass → "Build tree". Click an internal node
// in the tree to drill DOWN (re-root at that node). Click a breadcrumb chunk
// to drill UP. Hover a green leaf for patent titles + Wikipedia link.

const state = {
  taxon: null,       // {ottId, name, rank}
  cpc: "",
  limit: 300,
  lineage: [],       // root → ... → current root
};

const $ = sel => document.querySelector(sel);

// --------------------------------------------------------------------
// CPC dropdown — load once on page open
// --------------------------------------------------------------------
async function loadCpcOptions() {
  const sel = $("#cpc-select");
  const r = await fetch("/api/cpc?limit=200").then(r => r.json());
  for (const row of r.results) {
    const opt = document.createElement("option");
    opt.value = row.subclass;
    const fmt = n => n.toLocaleString();
    opt.textContent = `${row.subclass} — ${row.title || "(no title)"} ` +
                      `· ${fmt(row.n_patents)} pat / ${fmt(row.n_species)} sp`;
    sel.appendChild(opt);
  }
  sel.addEventListener("change", () => { state.cpc = sel.value; updateRunButton(); });
}

// --------------------------------------------------------------------
// Taxon autocomplete
// --------------------------------------------------------------------
const taxonInput = $("#taxon-input");
const taxonSuggest = $("#taxon-suggestions");
let suggestIdx = -1;
let suggestTimer = null;

taxonInput.addEventListener("input", () => {
  clearTimeout(suggestTimer);
  const q = taxonInput.value.trim();
  if (q.length < 2) { taxonSuggest.classList.remove("open"); return; }
  suggestTimer = setTimeout(() => doSearch(q), 180);
});

taxonInput.addEventListener("keydown", e => {
  const items = [...taxonSuggest.querySelectorAll("li")];
  if (!items.length) return;
  if (e.key === "ArrowDown") { e.preventDefault(); suggestIdx = (suggestIdx + 1) % items.length; renderActive(items); }
  else if (e.key === "ArrowUp") { e.preventDefault(); suggestIdx = (suggestIdx - 1 + items.length) % items.length; renderActive(items); }
  else if (e.key === "Enter" && suggestIdx >= 0) { e.preventDefault(); items[suggestIdx].click(); }
  else if (e.key === "Escape") { taxonSuggest.classList.remove("open"); }
});

function renderActive(items) {
  items.forEach((li, i) => li.classList.toggle("active", i === suggestIdx));
  items[suggestIdx]?.scrollIntoView({ block: "nearest" });
}

document.addEventListener("click", e => {
  if (!taxonInput.contains(e.target) && !taxonSuggest.contains(e.target)) {
    taxonSuggest.classList.remove("open");
  }
});

async function doSearch(q) {
  const r = await fetch(`/api/taxon/search?q=${encodeURIComponent(q)}&limit=15`).then(r => r.json());
  taxonSuggest.innerHTML = "";
  suggestIdx = -1;
  if (!r.results.length) { taxonSuggest.classList.remove("open"); return; }
  for (const t of r.results) {
    const li = document.createElement("li");
    li.innerHTML = `<span>${escapeHtml(t.name)}</span><span class="rank">${t.rank || ""}</span>`;
    li.addEventListener("click", () => selectTaxon(t));
    taxonSuggest.appendChild(li);
  }
  taxonSuggest.classList.add("open");
}

function selectTaxon(t) {
  state.taxon = t;
  taxonInput.value = t.name;
  taxonSuggest.classList.remove("open");
  $("#taxon-current").textContent = `${t.name} · ${t.rank || "?"} · ottId ${t.ottId}`;
  updateRunButton();
}

// --------------------------------------------------------------------
// Limit selector
// --------------------------------------------------------------------
$("#limit-select").addEventListener("change", e => { state.limit = parseInt(e.target.value, 10); });

function updateRunButton() { $("#run-btn").disabled = !state.taxon; }

// --------------------------------------------------------------------
// Build / re-root tree
// --------------------------------------------------------------------
$("#run-btn").addEventListener("click", () => { if (state.taxon) buildTree(state.taxon); });

async function buildTree(taxon) {
  state.taxon = taxon;
  taxonInput.value = taxon.name;
  $("#taxon-current").textContent = `${taxon.name} · ${taxon.rank || "?"} · ottId ${taxon.ottId}`;
  setStatus(`Querying graph for descendants of ${taxon.name}…`);
  const url = `/api/tree?ott=${taxon.ottId}` +
              (state.cpc ? `&cpc=${state.cpc}` : "") +
              `&limit=${state.limit}`;
  try {
    const t0 = performance.now();
    const [r, lineage] = await Promise.all([
      fetch(url).then(r => r.json()),
      fetch(`/api/lineage?ott=${taxon.ottId}`).then(r => r.json()),
    ]);
    const dt = (performance.now() - t0) / 1000;
    state.lineage = lineage.lineage || [];
    renderBreadcrumb();
    if (r.n_leaves === 0) {
      setStatus(`No descendant species of ${taxon.name} match this filter.`);
      clearTree();
    } else {
      const tr = r.truncated ? " (truncated)" : "";
      setStatus(`${r.n_leaves} leaves in ${dt.toFixed(1)}s${tr}. Hover a green leaf for patents · click an internal node to drill down.`);
      renderTree(r.tree);
    }
  } catch (e) {
    console.error(e);
    setStatus(`Error: ${e.message}`);
  }
}

function setStatus(s) { $("#status").textContent = s; }
function clearTree() { d3.select("#viz").selectAll("*").remove(); }

// --------------------------------------------------------------------
// Breadcrumb (lineage of current root)
// --------------------------------------------------------------------
function renderBreadcrumb() {
  const bc = $("#breadcrumb");
  bc.innerHTML = "";
  state.lineage.forEach((n, i) => {
    if (i > 0) {
      const sep = document.createElement("span");
      sep.className = "sep";
      sep.textContent = "›";
      bc.appendChild(sep);
    }
    const last = i === state.lineage.length - 1;
    const a = document.createElement("a");
    a.className = "crumb" + (last ? " current" : "");
    a.textContent = `${n.name}` + (n.rank ? ` (${n.rank})` : "");
    if (!last) a.addEventListener("click", () => buildTree(n));
    bc.appendChild(a);
  });
}

// --------------------------------------------------------------------
// D3 tree rendering
// --------------------------------------------------------------------
function renderTree(root) {
  clearTree();
  const hierarchy = d3.hierarchy(root, n => n.children || null);
  const leafCount = hierarchy.leaves().length;

  const depth = hierarchy.height + 1;
  const w = Math.max(900, depth * 240);
  const rowH = Math.max(16, Math.min(28, 800 / Math.max(20, leafCount)));
  const h = Math.max(400, leafCount * rowH);
  const margin = { top: 30, right: 300, bottom: 30, left: 80 };

  const svg = d3.select("#viz")
    .attr("width", w + margin.left + margin.right)
    .attr("height", h + margin.top + margin.bottom);

  const g = svg.append("g")
    .attr("transform", `translate(${margin.left},${margin.top})`);

  d3.cluster().size([h, w])(hierarchy);

  // Links
  g.selectAll(".link").data(hierarchy.links()).enter()
    .append("path")
    .attr("class", "link")
    .attr("d", d3.linkHorizontal().x(d => d.y).y(d => d.x));

  // Nodes
  const nodes = g.selectAll(".node").data(hierarchy.descendants()).enter()
    .append("g")
    .attr("class", d => "node " + (d.children ? "node--internal" : "node--species"))
    .attr("transform", d => `translate(${d.y},${d.x})`);

  nodes.append("circle").attr("r", d => d.children ? 4 : 5);

  nodes.append("text")
    .attr("dy", "0.32em")
    .attr("x", d => d.children ? -8 : 9)
    .attr("text-anchor", d => d.children ? "end" : "start")
    .text(d => labelFor(d.data));

  // Tooltip + click for LEAVES (species)
  nodes.filter(d => !d.children)
    .on("mouseover", (e, d) => showTooltip(e, d.data))
    .on("mousemove", e => moveTooltip(e))
    .on("mouseout", e => maybeHideTooltip(e));

  // Click-to-drill-down on INTERNAL nodes
  nodes.filter(d => d.children)
    .on("click", (e, d) => {
      if (d.data.ottId === state.taxon.ottId) return; // same root → no-op
      buildTree({ ottId: d.data.ottId, name: d.data.name, rank: d.data.rank });
    })
    .append("title")
    .text(d => `Click to drill down — re-root at "${d.data.name}"`);
}

function labelFor(n) {
  if (n.rank === "species") {
    const en = n.english ? ` — ${n.english}` : "";
    const edgeNote = n.n_edges ? ` · ${n.n_edges} pat` : "";
    return `${n.name}${en}${edgeNote}`;
  }
  return `${n.name} (${n.rank || "?"})`;
}

// --------------------------------------------------------------------
// Tooltip (must allow pointer events so links inside can be clicked).
// We delay hiding so the mouse can move from the leaf into the tooltip.
// --------------------------------------------------------------------
const tooltip = $("#tooltip");
let hideTimer = null;
tooltip.addEventListener("mouseenter", () => { clearTimeout(hideTimer); });
tooltip.addEventListener("mouseleave", () => { tooltip.style.display = "none"; });

function showTooltip(event, data) {
  clearTimeout(hideTimer);
  const patents = data.patents || [];
  const head = `<div class="tt-head"><i>${escapeHtml(data.name)}</i>` +
               (data.english ? ` &mdash; <span>${escapeHtml(data.english)}</span>` : "") +
               ` <span class="tt-rank">${data.rank || ""}</span></div>`;
  const links = data.wiki
    ? `<div class="tt-meta"><a href="${data.wiki}" target="_blank" rel="noopener">↗ Wikipedia (en)</a> · ${data.n_edges || patents.length} patent${(data.n_edges || patents.length) === 1 ? "" : "s"} match filter</div>`
    : `<div class="tt-meta">${data.n_edges || patents.length} patent${(data.n_edges || patents.length) === 1 ? "" : "s"} match filter</div>`;
  let body;
  if (!patents.length) {
    body = `<em>No patent titles cached for this leaf.</em>`;
  } else {
    const items = patents.map(p => {
      const title = escapeHtml(p.title || "(no title)");
      const date = formatDate(p.pubDate);
      const dateSpan = date ? ` <span class="tt-rank">${date}</span>` : "";
      return `<li><a href="${escapeAttr(p.url)}" target="_blank" rel="noopener">${title}</a>${dateSpan}</li>`;
    }).join("");
    body = `<ul>${items}</ul>`;
  }
  tooltip.innerHTML = head + body + links;
  tooltip.style.display = "block";
  moveTooltip(event);
}

function maybeHideTooltip(event) {
  // Give the user time to slide the cursor into the tooltip (so they can click links).
  hideTimer = setTimeout(() => { tooltip.style.display = "none"; }, 220);
}

function moveTooltip(event) {
  const pad = 14;
  let x = event.clientX + pad;
  let y = event.clientY + pad;
  const r = tooltip.getBoundingClientRect();
  if (x + r.width > window.innerWidth) x = event.clientX - r.width - pad;
  if (y + r.height > window.innerHeight) y = event.clientY - r.height - pad;
  tooltip.style.left = x + "px";
  tooltip.style.top = y + "px";
}

function formatDate(d) {
  if (d == null) return "";
  const s = String(d);
  if (s.length === 8) return `${s.slice(0,4)}-${s.slice(4,6)}-${s.slice(6,8)}`;
  return s;
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
function escapeAttr(s) { return escapeHtml(s); }

// --------------------------------------------------------------------
// URL-driven auto-build (for screenshots, deep-links).
// Example: /?ott=13867&name=Solanoideae&rank=subfamily&cpc=A61K&limit=15
// --------------------------------------------------------------------
async function autoBuildFromUrl() {
  const p = new URLSearchParams(window.location.search);
  const ott = parseInt(p.get("ott"), 10);
  if (!ott) return;
  const name = p.get("name") || `ottId ${ott}`;
  const rank = p.get("rank") || "";
  const cpc = p.get("cpc") || "";
  const limit = parseInt(p.get("limit") || "300", 10);
  if (cpc) {
    $("#cpc-select").value = cpc;
    state.cpc = cpc;
  }
  $("#limit-select").value = String(limit);
  state.limit = limit;
  await buildTree({ ottId: ott, name, rank });
}

// --------------------------------------------------------------------
// Init
// --------------------------------------------------------------------
(async () => {
  await loadCpcOptions();
  await autoBuildFromUrl();
})();
