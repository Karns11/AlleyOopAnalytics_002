/* Alley Oop Analytics frontend: vanilla JS + Chart.js. All aggregation is done server-side. */
"use strict";

// ------------------------------------------------------------------ constants

const STATS = [
  ["pts", "PTS"], ["reb", "REB"], ["ast", "AST"], ["stl", "STL"], ["blk", "BLK"],
  ["tov", "TOV"], ["pf", "PF"], ["min", "MIN"], ["fgm", "FGM"], ["fga", "FGA"],
  ["fg_pct", "FG%"], ["fg3m", "3PM"], ["fg3a", "3PA"], ["fg3_pct", "3P%"],
  ["ftm", "FTM"], ["fta", "FTA"], ["ft_pct", "FT%"],
];
const PCT = new Set(["fg_pct", "fg3_pct", "ft_pct", "efg_pct", "ts_pct"]);
const BOX_EXTRA = [["plus_minus", "+/-"], ["game_score", "GmSc"], ["ts_pct", "TS%"], ["efg_pct", "eFG%"]];
const STAT_LABEL = Object.fromEntries([...STATS, ...BOX_EXTRA]);
// Dark-mode categorical slots (validated order: blue, orange, aqua) and compare violet.
const SERIES = ["#3987e5", "#d95926", "#199e70"];
const INK = { text: "#c3c2b7", muted: "#898781", grid: "#2c2c2a", axis: "#383835", surface: "#232321" };

const state = {
  meta: null,
  player: null,          // {id, full_name, ...}
  season: "",
  start: "",
  end: "",
  lastN: 5,
  opponent: "",
  summary: null,
  games: [],
  sort: { key: "game_date", dir: -1 },
  rolling: false,
  compare: null,         // {player, games, summary}
};
const charts = {};
let metaReady;  // Promise<boolean>: resolves once /api/meta has loaded

// ------------------------------------------------------------------ helpers

const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function fmt(stat, v, { avg = false } = {}) {
  if (v === null || v === undefined) return "–";
  if (avg && !PCT.has(stat)) return (stat === "plus_minus" && v > 0 ? "+" : "") + v.toFixed(1);
  if (PCT.has(stat)) return (v * 100).toFixed(1);
  if (stat === "plus_minus") return (v > 0 ? "+" : "") + (Number.isInteger(v) ? v : v.toFixed(1));
  if (stat === "min" && !Number.isInteger(v)) return v.toFixed(1);
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
}

function fmtDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "2-digit" });
}

function teamName(abbr) {
  return (state.meta && state.meta.teams[abbr]) || abbr;
}

async function api(path, params = {}) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== "" && v !== null && v !== undefined) qs.set(k, v);
  const url = `/api${path}${qs.toString() ? "?" + qs : ""}`;
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : (body.detail?.[0]?.msg || detail);
    } catch (_) { /* not JSON */ }
    throw new Error(detail);
  }
  return res.json();
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

function showError(msg) {
  const e = $("error");
  e.textContent = msg || "";
  e.hidden = !msg;
}

/** Apply color class + direction glyph + tooltip to a stat cell. */
function colorCell(td, stat, color, value, baseline) {
  if (!color) return td;
  td.classList.add(`c-${color}`);
  if (color !== "yellow") {
    const higher = baseline !== null && baseline !== undefined && value > baseline;
    td.classList.add(higher ? "up" : "down");
  }
  if (baseline !== null && baseline !== undefined && value !== null && value !== undefined) {
    const diff = PCT.has(stat)
      ? `${((value - baseline) * 100).toFixed(1)} pts`
      : baseline ? `${(((value - baseline) / Math.abs(baseline)) * 100).toFixed(0)}%` : `${value - baseline}`;
    td.title = `Season avg ${fmt(stat, baseline)} (${diff.startsWith("-") ? "" : "+"}${diff})`;
  }
  return td;
}

// ------------------------------------------------------------------ URL state

function writeHash() {
  if (!state.player) return;
  const p = new URLSearchParams({ p: state.player.id });
  if (state.season) p.set("season", state.season);
  if (state.start) p.set("start", state.start);
  if (state.end) p.set("end", state.end);
  if (state.lastN !== state.meta.default_last_n) p.set("n", state.lastN);
  if (state.opponent) p.set("opp", state.opponent);
  if (state.compare) p.set("cmp", state.compare.player.id);
  history.replaceState(null, "", "#" + p.toString());
}

function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  const id = parseInt(p.get("p"), 10);
  return {
    id: Number.isFinite(id) && id > 0 ? id : null,
    season: p.get("season"),
    start: p.get("start") || "",
    end: p.get("end") || "",
    n: parseInt(p.get("n"), 10),
    opp: p.get("opp") || "",
    cmp: parseInt(p.get("cmp"), 10),
  };
}

// ------------------------------------------------------------------ autocomplete

function setupAutocomplete(input, list, onPick) {
  let items = [];
  let active = -1;
  let seq = 0;

  const close = () => { list.hidden = true; input.setAttribute("aria-expanded", "false"); active = -1; };
  const highlight = (i) => {
    active = i;
    [...list.children].forEach((li, j) => li.setAttribute("aria-selected", j === i ? "true" : "false"));
    if (list.children[i]) list.children[i].scrollIntoView({ block: "nearest" });
  };
  const pick = (i) => { if (items[i]) { close(); input.value = ""; onPick(items[i]); } };

  const run = debounce(async () => {
    const q = input.value.trim();
    if (q.length < 2 || !/^[\p{L}\p{M} .'\-]+$/u.test(q)) { close(); return; }
    const mine = ++seq;
    try {
      const data = await api("/players/search", { q });
      if (mine !== seq) return;
      items = data.results;
      list.replaceChildren(...items.map((p, i) => el("li", {
        role: "option", id: `${list.id}-${i}`,
        onmousedown: (e) => { e.preventDefault(); pick(i); },
      }, el("span", { text: p.full_name }),
         el("span", { class: "tag", text: p.has_games ? (p.team_abbr || "") : "no recent games" }))));
      if (!items.length) list.replaceChildren(el("li", { class: "tag", text: "No players found" }));
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
      highlight(items.length ? 0 : -1);
    } catch (e) {
      close();
    }
  }, 200);

  input.addEventListener("input", run);
  input.addEventListener("blur", () => setTimeout(close, 100));
  input.addEventListener("keydown", (e) => {
    if (list.hidden) return;
    if (e.key === "ArrowDown") { e.preventDefault(); highlight(Math.min(active + 1, items.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); highlight(Math.max(active - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); pick(active); }
    else if (e.key === "Escape") close();
  });
}

// ------------------------------------------------------------------ data loading

function rangeParams() {
  return { season: state.season, start: state.start, end: state.end };
}

let loadSeq = 0;
async function loadPlayer({ keepOpponent = false } = {}) {
  if (!state.player) return;
  const mine = ++loadSeq;
  showError("");
  $("empty").hidden = true;
  $("content").hidden = false;
  try {
    const [opps, games] = await Promise.all([
      api(`/players/${state.player.id}/opponents`, rangeParams()),
      api(`/players/${state.player.id}/games`, rangeParams()),
    ]);
    if (mine !== loadSeq) return;
    if (!keepOpponent || !opps.opponents.some((o) => o.abbr === state.opponent)) state.opponent = "";
    state.opponents = opps.opponents;
    state.games = games.games;
    state.player = games.player;
    await loadSummary();
    if (state.compare) await loadCompare(state.compare.player);
    renderAll();
  } catch (e) {
    showError(`Couldn't load player: ${e.message}`);
  }
  writeHash();
}

async function loadSummary() {
  state.summary = await api(`/players/${state.player.id}/summary`, {
    ...rangeParams(), last_n: state.lastN, opponent: state.opponent,
  });
}

async function loadCompare(player) {
  const [games, summary] = await Promise.all([
    api(`/players/${player.id}/games`, rangeParams()),
    api(`/players/${player.id}/summary`, rangeParams()),
  ]);
  state.compare = { player: games.player, games: games.games, summary };
  $("compare-clear").hidden = false;
  $("compare").placeholder = `Comparing: ${games.player.full_name}`;
}

// ------------------------------------------------------------------ rendering

function renderAll() {
  renderPlayerCard();
  renderLegend();
  renderSummary();
  renderAdvanced();
  renderHighlights();
  renderBoxScores();
  renderCharts();
}

function renderPlayerCard() {
  const { player, season_avg: sa, baseline_season: bs, streak } = state.summary;
  $("player-name").textContent = player.full_name;
  $("player-meta").textContent = [player.team_name || player.team_abbr, player.is_active ? null : "Inactive",
    bs ? `${bs} regular season${sa ? ` · ${sa.games} GP` : ""}` : null].filter(Boolean).join(" · ");
  const f = (k) => fmt(k, sa[k], { avg: true });
  const items = sa ? [["PTS", f("pts")], ["REB", f("reb")], ["AST", f("ast")],
    ["FG%", f("fg_pct")], ["3P%", f("fg3_pct")], ["MIN", f("min")]] : [];
  $("headline").replaceChildren(...items.map(([k, v]) => el("div", {}, el("dt", { text: k }), el("dd", { text: v }))));

  const s = $("streak");
  if (streak) {
    const icon = { hot: "🔥 Hot", cold: "🧊 Cold", steady: "• Steady" }[streak.status];
    const pct = Math.round(streak.delta_pct * 100);
    s.className = `streak ${streak.status}`;
    s.textContent = `${icon}: last ${streak.window} games ${pct >= 0 ? "+" : ""}${pct}% vs season avg (PTS/REB/AST)`;
    s.title = `Last ${streak.window}: ${streak.last.pts} PTS, ${streak.last.reb} REB, ${streak.last.ast} AST`;
    s.hidden = false;
  } else {
    s.hidden = true;
  }
  $("baseline-label").textContent = bs || "season";
}

function renderLegend() {
  const t = state.meta.thresholds;
  const pct = Math.round(t.pct * 100);
  const pts = Math.round(t.pct_points * 100);
  $("legend").replaceChildren(
    el("span", {}, el("i", { class: "sw-green" }), `▲ Above avg by >${pct}%`),
    el("span", {}, el("i", { class: "sw-yellow" }), `• Within ±${pct}%`),
    el("span", {}, el("i", { class: "sw-red" }), `▼ Below by >${pct}%`),
    el("span", { class: "muted", text: `Shooting %: ±${pts} pts. TOV & PF: lower is better.` }),
  );
}

function summaryRow(label, avg, baseline, { isBaseline = false, labelNode = null } = {}) {
  const first = el("td", {}, labelNode || label);
  if (!avg) {
    return el("tr", {}, first, el("td", { class: "empty-row", colspan: STATS.length + 1, text: "No games" }));
  }
  const cells = STATS.map(([k]) => {
    const td = el("td", { text: fmt(k, avg[k], { avg: true }) });
    return isBaseline ? td : colorCell(td, k, avg.colors[k], avg[k], baseline[k]);
  });
  return el("tr", { class: isBaseline ? "baseline" : null }, first, el("td", { text: avg.games }), ...cells);
}

function renderSummary() {
  const { splits, season_avg: sa, baseline_season: bs } = state.summary;
  const base = sa || {};

  const lastNInput = el("input", { type: "number", min: 1, max: 82, value: state.lastN, "aria-label": "Number of recent games" });
  lastNInput.addEventListener("change", async () => {
    const n = Math.max(1, Math.min(82, parseInt(lastNInput.value, 10) || 5));
    state.lastN = n;
    $("rolling-n").textContent = n;
    await refreshSummaryOnly();
    renderCharts();
  });

  const oppSelect = el("select", { "aria-label": "Opponent" },
    el("option", { value: "", text: "Select team" }),
    ...(state.opponents || []).map((o) => el("option", { value: o.abbr, selected: o.abbr === state.opponent, text: `${o.abbr} (${o.games})` })));
  oppSelect.addEventListener("change", async () => {
    state.opponent = oppSelect.value;
    await refreshSummaryOnly();
  });

  const head = el("thead", {}, el("tr", {}, el("th", { text: "Split" }), el("th", { text: "GP" }),
    ...STATS.map(([, l]) => el("th", { text: l }))));
  const body = el("tbody", {},
    summaryRow(`Season avg${bs ? ` (${bs})` : ""}`, sa, base, { isBaseline: true }),
    summaryRow("Overall (range)", splits.overall, base),
    summaryRow("", splits.last_n, base, { labelNode: el("span", { class: "row-ctl" }, "Last", lastNInput, "games") }),
    summaryRow("Home", splits.home, base),
    summaryRow("Away", splits.away, base),
    state.opponent
      ? summaryRow("", splits.vs_opponent, base, { labelNode: el("span", { class: "row-ctl" }, "Vs", oppSelect) })
      : el("tr", {}, el("td", {}, el("span", { class: "row-ctl" }, "Vs", oppSelect)),
          el("td", { class: "empty-row", colspan: STATS.length + 1, text: "Pick an opponent" })),
    summaryRow("Wins", splits.wins, base),
    summaryRow("Losses", splits.losses, base),
    summaryRow("Back-to-back", splits.b2b, base),
    summaryRow("Rested", splits.rested, base),
  );
  $("summary").replaceChildren(head, body);
}

async function refreshSummaryOnly() {
  try {
    await loadSummary();
    renderSummary();
    renderAdvanced();
    writeHash();
  } catch (e) {
    showError(`Couldn't refresh summary: ${e.message}`);
  }
}

function renderAdvanced() {
  const a = state.summary.advanced;
  const sa = state.summary.season_advanced;
  if (!a) { $("advanced").replaceChildren(el("p", { class: "muted", text: "No games in range." })); return; }
  const kv = (label, val, seasonVal) => el("div", {}, el("dt", { text: label }),
    el("dd", {}, val ?? "–", seasonVal !== undefined && seasonVal !== null ? el("small", { text: `season ${seasonVal}` }) : null));
  const p = (v) => (v === null || v === undefined ? null : (v * 100).toFixed(1) + "%");
  const items = [
    kv("TS%", p(a.ts_pct), p(sa?.ts_pct)),
    kv("eFG%", p(a.efg_pct), p(sa?.efg_pct)),
    kv("Game Score", a.game_score, sa?.game_score),
    kv("AST/TOV", a.ast_to, sa?.ast_to),
  ];
  if (a.per36) {
    for (const k of ["pts", "reb", "ast", "stl", "blk", "tov"]) {
      items.push(kv(`${STAT_LABEL[k]} / 36`, a.per36[k], sa?.per36?.[k]));
    }
  }
  $("advanced").replaceChildren(...items);
}

function renderHighlights() {
  const hs = state.summary.highlights;
  const seasons = state.meta.seasons;
  const since = seasons.length ? seasons[seasons.length - 1] : "";
  if (!hs.length) { $("highlights").replaceChildren(el("li", { class: "muted", text: "No season-high games in this range." })); return; }
  $("highlights").replaceChildren(...hs.map((h) => el("li", {},
    el("span", {}, el("strong", { text: `${h.value} ${STAT_LABEL[h.stat]}` }),
      ` ${h.is_home ? "vs" : "@"} ${h.opponent_abbr} · ${fmtDate(h.game_date)}`),
    el("span", { class: `kind ${h.kind}`, title: h.kind === "career" ? `Best since ${since} (ingested data)` : "Season high",
      text: h.kind === "career" ? `High since ${since}` : "Season high" }))));
}

const BOX_COLUMNS = [
  ["game_date", "Date"], ["opponent_abbr", "Opp"], ["is_home", "H/A"], ["wl", "W/L"],
  ...STATS, ...BOX_EXTRA,
];

function sortedGames() {
  const { key, dir } = state.sort;
  return [...state.games].sort((a, b) => {
    const av = a[key], bv = b[key];
    if (av === bv) return a.game_date < b.game_date ? 1 : -1;
    if (av === null || av === undefined) return 1;
    if (bv === null || bv === undefined) return -1;
    return (av < bv ? -1 : 1) * dir;
  });
}

function renderBoxScores() {
  const base = state.summary.season_avg || {};
  $("game-count").textContent = `(${state.games.length} game${state.games.length === 1 ? "" : "s"})`;
  const head = el("thead", {}, el("tr", {}, ...BOX_COLUMNS.map(([k, l]) => {
    const th = el("th", { text: l, scope: "col", tabindex: 0 });
    if (state.sort.key === k) th.setAttribute("aria-sort", state.sort.dir === 1 ? "ascending" : "descending");
    const go = () => {
      state.sort = { key: k, dir: state.sort.key === k ? -state.sort.dir : (k === "game_date" || k === "opponent_abbr" ? 1 : -1) };
      renderBoxScores();
    };
    th.addEventListener("click", go);
    th.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
    return th;
  })));
  const rows = sortedGames().map((g) => el("tr", {},
    el("td", { class: "txt" }, fmtDate(g.game_date),
      g.season_type === "Playoffs" ? el("span", { class: "badge", title: "Playoffs", text: "PO" }) : null,
      g.b2b ? el("span", { class: "badge", title: "Second night of a back-to-back", text: "B2B" }) : null),
    el("td", { class: "txt", title: teamName(g.opponent_abbr), text: g.opponent_abbr }),
    el("td", { class: "txt", text: g.is_home ? "Home" : "Away" }),
    el("td", { class: "txt", text: g.wl || "" }),
    ...STATS.map(([k]) => {
      const td = el("td", { text: fmt(k, g[k]) });
      return colorCell(td, k, g.colors[k], g[k], base[k]);
    }),
    colorCell(el("td", { text: fmt("plus_minus", g.plus_minus) }), "plus_minus", g.colors.plus_minus, g.plus_minus, base.plus_minus),
    el("td", { text: fmt("game_score", g.game_score) }),
    el("td", { text: fmt("ts_pct", g.ts_pct) }),
    el("td", { text: fmt("efg_pct", g.efg_pct) }),
  ));
  $("boxscores").replaceChildren(head, el("tbody", {}, rows.length ? rows :
    el("tr", {}, el("td", { colspan: BOX_COLUMNS.length, class: "txt muted", text: "No games in this range." }))));
}

function exportCsv() {
  if (!state.games.length) return;
  const cols = ["game_date", "season", "season_type", "team_abbr", "opponent_abbr", "is_home", "wl",
    "min", "pts", "reb", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf", "fgm", "fga", "fg_pct",
    "fg3m", "fg3a", "fg3_pct", "ftm", "fta", "ft_pct", "plus_minus", "game_score", "ts_pct", "efg_pct", "b2b"];
  const esc = (v) => {
    if (v === null || v === undefined) return "";
    let s = String(v);
    if (/^[=+\-@]/.test(s) && isNaN(Number(s))) s = "'" + s; // guard against CSV formula injection
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [cols.join(","), ...sortedGames().map((g) =>
    cols.map((c) => esc(c === "is_home" ? (g.is_home ? "home" : "away") : g[c])).join(","))];
  const blob = new Blob([lines.join("\n") + "\n"], { type: "text/csv" });
  const slug = state.player.full_name.toLowerCase().replace(/[^a-z0-9]+/g, "-");
  const suffix = [state.season || "all", state.start, state.end].filter(Boolean).join("_");
  const a = el("a", { href: URL.createObjectURL(blob), download: `${slug}_${suffix}.csv` });
  document.body.append(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 0);
}

// ------------------------------------------------------------------ charts

const CHARTS = [
  { id: "chart-prd", stats: ["pts", "reb", "ast"] },
  { id: "chart-min", stats: ["min"] },
  { id: "chart-pct", stats: ["fg_pct", "fg3_pct", "ft_pct"], pct: true },
  { id: "chart-vol", stats: ["fga", "fg3a", "fta"] },
  { id: "chart-pm", stats: ["plus_minus"], zero: true },
];
const ATTEMPTS = { fg_pct: "fga", fg3_pct: "fg3a", ft_pct: "fta" };

function rolling(points, n) {
  const out = [];
  const win = [];
  for (const p of points) {
    if (p.y !== null) win.push(p.y);
    if (win.length > n) win.shift();
    out.push({ x: p.x, y: win.length ? win.reduce((s, v) => s + v, 0) / win.length : null });
  }
  return out;
}

function seriesPoints(games, stat) {
  return [...games].sort((a, b) => (a.game_date < b.game_date ? -1 : 1)).map((g) => {
    let y = g[stat];
    if (ATTEMPTS[stat] && !g[ATTEMPTS[stat]]) y = null;
    if (y !== null && y !== undefined && PCT.has(stat)) y = y * 100;
    return { x: g.game_date, y: y ?? null };
  });
}

function buildDatasets(cfg, labels) {
  const sets = [];
  const players = [{ games: state.games, summary: state.summary, name: state.player.full_name, dash: [] }];
  if (state.compare) {
    players.push({ games: state.compare.games, summary: state.compare.summary, name: state.compare.player.full_name, dash: [2, 3] });
  }
  const multi = players.length > 1;
  players.forEach((pl, pi) => {
    cfg.stats.forEach((stat, si) => {
      const color = SERIES[si];
      const label = `${STAT_LABEL[stat]}${multi ? ` · ${pl.name}` : ""}`;
      const pts = seriesPoints(pl.games, stat);
      const pointStyle = pi === 0 ? "circle" : "triangle";
      if (state.rolling) {
        sets.push({ label: `${label} (game)`, data: pts, borderColor: color, backgroundColor: color + "66",
          showLine: false, pointRadius: 2.5, pointHoverRadius: 5, pointStyle, _raw: true });
        sets.push({ label: `${label} (${state.lastN}-gm avg)`, data: rolling(pts, state.lastN), borderColor: color,
          backgroundColor: color, borderWidth: 2, borderDash: pl.dash, pointRadius: 0, pointHoverRadius: 4, tension: 0.25, spanGaps: true });
      } else {
        sets.push({ label, data: pts, borderColor: color, backgroundColor: color, borderWidth: 2, borderDash: pl.dash,
          pointRadius: labels.length > 60 ? 0 : 2.5, pointHoverRadius: 5, pointStyle, tension: 0.15, spanGaps: true });
      }
      const avg = pl.summary.season_avg?.[stat];
      if (avg !== null && avg !== undefined && pi === 0) {
        const y = PCT.has(stat) ? avg * 100 : avg;
        sets.push({ label: `${STAT_LABEL[stat]} season avg`, data: labels.map((x) => ({ x, y })), borderColor: color + "b3",
          borderWidth: 1.5, borderDash: [6, 5], pointRadius: 0, pointHoverRadius: 0, _avg: true });
      }
    });
  });
  return sets;
}

function renderCharts() {
  if (typeof Chart === "undefined") return;
  $("rolling-n").textContent = state.lastN;
  const allGames = state.compare ? [...state.games, ...state.compare.games] : state.games;
  const labels = [...new Set(allGames.map((g) => g.game_date))].sort();
  for (const cfg of CHARTS) {
    const datasets = buildDatasets(cfg, labels);
    if (charts[cfg.id]) {
      charts[cfg.id].data.labels = labels;
      charts[cfg.id].data.datasets = datasets;
      charts[cfg.id].update();
      continue;
    }
    charts[cfg.id] = new Chart($(cfg.id), {
      type: "line",
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: {
            ticks: { color: INK.muted, maxRotation: 0, autoSkip: true, maxTicksLimit: 8, callback(v) { return fmtDate(this.getLabelForValue(v)); } },
            grid: { display: false }, border: { color: INK.axis },
          },
          y: {
            beginAtZero: !cfg.zero, suggestedMax: cfg.pct ? 100 : undefined,
            ticks: { color: INK.muted, callback: (v) => (cfg.pct ? `${v}%` : v) },
            grid: { color: (ctx) => (cfg.zero && ctx.tick.value === 0 ? INK.axis : INK.grid) },
            border: { display: false },
          },
        },
        plugins: {
          legend: {
            labels: {
              color: INK.text, usePointStyle: true, boxWidth: 8, boxHeight: 8,
              filter: (item, data) => !data.datasets[item.datasetIndex]._raw,
            },
          },
          tooltip: {
            backgroundColor: "#111", borderColor: "#383835", borderWidth: 1, titleColor: "#f0efec", bodyColor: "#c3c2b7",
            filter: (item) => !item.dataset._avg && item.parsed.y !== null,
            callbacks: {
              title: (items) => (items.length ? fmtDate(items[0].label) : ""),
              label: (item) => {
                const v = item.parsed.y;
                const txt = cfg.pct ? `${v.toFixed(1)}%` : (Number.isInteger(v) ? v : v.toFixed(1));
                return ` ${item.dataset.label}: ${txt}`;
              },
            },
          },
        },
      },
    });
  }
}

// ------------------------------------------------------------------ init

async function selectPlayer(p) {
  if (!(await metaReady)) return;
  state.player = p;
  state.opponent = "";
  $("search").value = "";
  $("search").placeholder = p.full_name;
  loadPlayer();
}

async function init() {
  // Attach search listeners immediately so typing during the /api/meta fetch isn't lost.
  setupAutocomplete($("search"), $("search-results"), selectPlayer);
  setupAutocomplete($("compare"), $("compare-results"), async (p) => {
    if (!(await metaReady) || !state.player) return;
    try { await loadCompare(p); renderCharts(); writeHash(); } catch (e) { showError(`Couldn't load comparison: ${e.message}`); }
  });

  metaReady = api("/meta").then((m) => { state.meta = m; return true; }, (e) => {
    $("empty").replaceChildren(el("h2", { text: "Data unavailable" }), el("p", { text: e.message }));
    return false;
  });
  if (!(await metaReady)) return;
  const m = state.meta;
  state.lastN = m.default_last_n;
  $("last-updated").textContent = m.last_updated ? new Date(m.last_updated).toLocaleString() : "never";
  $("season").replaceChildren(...m.seasons.map((s) => el("option", { value: s, text: s })),
    el("option", { value: "", text: "All seasons" }));
  state.season = m.seasons[0] || "";

  const h = readHash();
  if (h.season !== null && (h.season === "" || m.seasons.includes(h.season))) state.season = h.season;
  if (/^\d{4}-\d{2}-\d{2}$/.test(h.start)) state.start = h.start;
  if (/^\d{4}-\d{2}-\d{2}$/.test(h.end)) state.end = h.end;
  if (h.n >= 1 && h.n <= 82) state.lastN = h.n;
  if (/^[A-Z]{3}$/.test(h.opp)) state.opponent = h.opp;
  $("season").value = state.season;
  $("start").value = state.start;
  $("end").value = state.end;

  const onRangeChange = () => {
    state.season = $("season").value;
    state.start = $("start").value;
    state.end = $("end").value;
    if (state.start && state.end && state.start > state.end) { showError("Start date must be on or before end date."); return; }
    loadPlayer({ keepOpponent: true });
  };
  $("season").addEventListener("change", onRangeChange);
  $("start").addEventListener("change", onRangeChange);
  $("end").addEventListener("change", onRangeChange);
  $("clear-dates").addEventListener("click", () => { $("start").value = ""; $("end").value = ""; onRangeChange(); });
  $("filters").addEventListener("submit", (e) => e.preventDefault());
  $("export-csv").addEventListener("click", exportCsv);
  $("rolling").addEventListener("change", (e) => { state.rolling = e.target.checked; renderCharts(); });
  $("compare-clear").addEventListener("click", () => {
    state.compare = null;
    $("compare-clear").hidden = true;
    $("compare").placeholder = "Compare with…";
    renderCharts();
    writeHash();
  });

  // Quick-start suggestions
  const sugg = ["LeBron James", "Stephen Curry", "Nikola Jokić", "Victor Wembanyama"];
  $("suggestions").replaceChildren("Try: ", ...sugg.map((name) => el("button", {
    type: "button", class: "chip", text: name,
    onclick: async () => {
      const r = await api("/players/search", { q: name.normalize("NFD").replace(/\p{M}/gu, "") });
      if (r.results[0]) selectPlayer(r.results[0]);
    },
  })));

  if (h.id) {
    state.player = { id: h.id, full_name: "" };
    if (h.cmp > 0) {
      try { await loadCompare({ id: h.cmp }); } catch (_) { state.compare = null; }
    }
    loadPlayer({ keepOpponent: true });
  }
}

document.addEventListener("DOMContentLoaded", init);
