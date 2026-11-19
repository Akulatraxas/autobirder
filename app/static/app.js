"use strict";
// No HTML string injection anywhere: all dynamic content goes through textContent / createElement.

const LOOKAHEAD = 5;
const PRELOAD_IMAGES = 2;

const $ = (id) => document.getElementById(id);
const state = {
  images: [], idx: -1, detail: null, selected: -1, keywords: [], autoKeywords: [], touched: false,
  token: 0, pollTimer: null, hideDone: false,
};

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "text") node.textContent = v;
    else if (k === "class") node.className = v;
    else node.setAttribute(k, v);
  }
  for (const c of children) node.appendChild(c);
  return node;
}

async function api(path, body) {
  const opts = body === undefined
    ? { method: "GET" }
    : { method: "POST", headers: { "Content-Type": "application/json", "X-Autobirder": "1" }, body: JSON.stringify(body) };
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error("HTTP " + res.status);
  return res.json();
}

let toastTimer = null;
function toast(msg, isErr = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = isErr ? "err" : "";
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, isErr ? 4000 : 1400);
}

// ---------- model status ----------
let modelStatus = null;
let modelPollTimer = null;

function clearModelPoll() {
  if (modelPollTimer !== null) {
    clearTimeout(modelPollTimer);
    modelPollTimer = null;
  }
}

function scheduleModelPoll() {
  if (modelPollTimer !== null) return;
  modelPollTimer = setTimeout(async () => {
    modelPollTimer = null;
    await pollModelStatus();
  }, 1000);
}

function applyModelStatus(m) {
  if (!m) return;
  const badge = $("model-status");
  const prev = modelStatus;
  modelStatus = m.status;
  badge.hidden = false;
  if (m.status === "loading") {
    const secs = typeof m.elapsed_s === "number" ? ` (${m.elapsed_s}s)` : "";
    badge.textContent = `Model loading${secs}…`;
    badge.className = "model-status loading";
    badge.title = "Loading BioCLIP model (first run downloads ~4 GB of weights).";
    scheduleModelPoll();
  } else if (m.status === "ready") {
    badge.textContent = "Model ready";
    badge.className = "model-status ready";
    badge.title = "BioCLIP model is loaded and ready.";
    clearModelPoll();
    if (prev === "loading") toast("BioCLIP model ready");
  } else {
    badge.textContent = "Model load failed";
    badge.className = "model-status err";
    badge.title = "Failed to load BioCLIP model. Check server logs.";
    clearModelPoll();
  }
}

async function pollModelStatus() {
  clearModelPoll();
  try {
    const d = await api("/api/status");
    applyModelStatus(d.model);
  } catch (e) {
    scheduleModelPoll();
  }
}

// ---------- list ----------
function renderList() {
  const nav = $("list");
  nav.replaceChildren();
  state.images.forEach((img, i) => {
    const row = el("div", { class: "row", "data-i": String(i) }, [
      el("span", { class: "p", text: img.path, title: img.path }),
      el("span", { class: "m" }),
    ]);
    row.addEventListener("click", () => go(i));
    nav.appendChild(row);
    img.row = row;
    updateRow(i);
  });
  updateProgress();
}

function updateRow(i) {
  const img = state.images[i];
  if (!img || !img.row) return;
  const done = img.mark === "saved" || img.mark === "skipped";
  img.row.className = "row" + (img.mark ? " " + img.mark : "") + (i === state.idx ? " active" : "") +
    (state.hideDone && done && i !== state.idx ? " hidden" : "");
}

function updateProgress() {
  const done = state.images.filter((i) => i.mark).length;
  $("progress").textContent = state.images.length
    ? `${state.idx + 1} / ${state.images.length}  ·  ${done} done`
    : "no images";
}

// ---------- navigation ----------
function isDone(i) { return !!state.images[i].mark; }

function nextUnmarked(from) {
  for (let i = from + 1; i < state.images.length; i++) if (!isDone(i)) return i;
  return -1;
}

function go(i) {
  if (i < 0 || i >= state.images.length) return;
  const prev = state.idx;
  state.idx = i;
  if (prev >= 0) updateRow(prev);
  updateRow(i);
  state.images[i].row.scrollIntoView({ block: "nearest" });
  load(i);
}

async function load(i) {
  const token = ++state.token;
  clearTimeout(state.pollTimer);
  const img = state.images[i];
  state.detail = null;
  state.selected = -1;
  state.touched = false;
  $("curpath").textContent = img.path;
  $("photo").src = `/api/images/${img.id}/preview`;
  $("photo").alt = img.path;
  state.autoKeywords = [];
  setKeywords([]);
  $("f-description").value = "";
  $("f-comment").value = "";
  renderCandidates();
  $("status").textContent = "Loading…";
  $("current").textContent = "";
  updateProgress();

  api("/api/prioritize", { ids: state.images.slice(i, i + 1 + LOOKAHEAD).map((x) => x.id) }).catch(() => {});
  for (let k = 1; k <= PRELOAD_IMAGES; k++) {
    const n = state.images[i + k];
    if (n) new Image().src = `/api/images/${n.id}/preview`;
  }
  await refresh(i, token);
}

async function refresh(i, token) {
  let d;
  try {
    d = await api(`/api/images/${state.images[i].id}`);
  } catch (e) {
    if (token === state.token) $("status").textContent = "Failed to load image info.";
    return;
  }
  if (token !== state.token) return;
  state.detail = d;
  if (d.model) applyModelStatus(d.model);
  renderCandidates();
  renderCurrent();
  const modelState = d.model ? d.model.status : modelStatus;
  if (d.status === "ready") {
    $("status").textContent = d.candidates.length ? "" : "No guesses returned.";
    if (!state.touched && d.candidates.length) selectCandidate(0, true);
  } else if (d.status === "error") {
    $("status").textContent = modelState === "error"
      ? "Model failed to load. Enter tags manually (N) or check logs."
      : "Classification failed. Enter tags manually (N).";
  } else {
    if (d.status === "classifying") {
      $("status").textContent = modelState === "loading"
        ? "Loading model (downloading on first run)…"
        : "Classifying…";
    } else {
      $("status").textContent = modelState === "loading"
        ? "Waiting for model to load (downloading on first run)…"
        : "Queued…";
    }
    state.pollTimer = setTimeout(() => refresh(i, token), 600);
  }
}

// ---------- candidates ----------
function renderCandidates() {
  const ol = $("candidates");
  ol.replaceChildren();
  const cands = state.detail ? state.detail.candidates : [];
  cands.forEach((c, n) => {
    const title = c.common_name || c.scientific_name || "Unknown";
    const li = el("li", { class: "cand" + (n === state.selected ? " sel" : "") }, [
      el("span", { class: "key", text: n < 9 ? String(n + 1) : "0" }),
      el("span", { class: "name" }, [
        el("b", { text: title }),
        el("i", { text: c.common_name ? c.scientific_name : "" }),
      ]),
      el("span", { class: "score", text: (c.score * 100).toFixed(1) + "%" }),
    ]);
    const bar = el("span", { class: "bar" });
    bar.style.width = Math.max(0, Math.min(100, c.score * 100)) + "%";
    li.appendChild(bar);
    li.addEventListener("click", () => selectCandidate(n, true));
    ol.appendChild(li);
  });
  $("nonebtn").classList.toggle("sel", state.selected === -2);
}

function selectCandidate(n, fill) {
  const cands = state.detail ? state.detail.candidates : [];
  if (n < 0 || n >= cands.length) return;
  state.selected = n;
  if (fill) fillFrom(cands[n]);
  renderCandidates();
}

const sameKw = (a, b) => a.toLowerCase() === b.toLowerCase();

// Keywords the app generated from the selected guess. Everything else in the list was
// added by the user and must survive picking a different guess.
function manualKeywords() {
  return state.keywords.filter((k) => !state.autoKeywords.some((a) => sameKw(a, k)));
}

function fillFrom(c) {
  const common = c.common_name, sci = c.scientific_name;
  $("f-description").value = common && sci ? `${common} (${sci})` : (common || sci);
  const auto = [common, c.genus].filter(Boolean);
  const manual = manualKeywords().filter((k) => !auto.some((a) => sameKw(a, k)));
  state.autoKeywords = auto;
  setKeywords([...auto, ...manual]);
  $("f-comment").value =
    `BioCLIP: ${common || sci} [${sci}], Confidence: ${(c.score * 100).toFixed(2)}%`;
  state.touched = false;
}

function selectNone() {
  state.selected = -2;
  $("f-description").value = "";
  $("f-comment").value = "";
  const manual = manualKeywords();
  state.autoKeywords = [];
  setKeywords(manual);
  state.touched = true;
  renderCandidates();
  $("f-description").focus();
}

function renderCurrent() {
  const cur = state.detail && state.detail.current;
  $("current").textContent = cur
    ? `Existing tags (U to load): ${cur.description || "—"} | ${cur.keywords.join(", ") || "—"}`
    : "No existing tags.";
}

function useCurrent() {
  const cur = state.detail && state.detail.current;
  if (!cur) return;
  $("f-description").value = cur.description;
  $("f-comment").value = cur.comment;
  state.autoKeywords = [];
  setKeywords(cur.keywords);
  state.touched = true;
}

// ---------- keywords ----------
function setKeywords(list) {
  state.keywords = list.slice();
  renderChips();
}

function renderChips() {
  const box = $("chips");
  for (const c of Array.from(box.querySelectorAll(".chip"))) c.remove();
  const input = $("f-keyword");
  state.keywords.forEach((kw, n) => {
    const rm = el("button", { type: "button", "aria-label": "remove " + kw, tabindex: "-1", text: "×" });
    rm.addEventListener("click", () => { state.keywords.splice(n, 1); state.touched = true; renderChips(); });
    box.insertBefore(el("span", { class: "chip" }, [el("span", { text: kw }), rm]), input);
  });
  if (document.activeElement === input) loadSuggestions();
}

// ---------- keyword autocomplete ----------
const sugg = { items: [], active: -1, token: 0 };

async function loadSuggestions() {
  const token = ++sugg.token;
  await null; // let callers clear the input first
  const params = new URLSearchParams({ q: $("f-keyword").value.trim() });
  for (const k of state.keywords) params.append("exclude", k);
  try {
    const d = await api("/api/keywords?" + params.toString());
    if (token !== sugg.token || document.activeElement !== $("f-keyword")) return;
    sugg.items = d.keywords;
  } catch (e) {
    sugg.items = [];
  }
  sugg.active = -1;
  renderSuggestions();
}

function renderSuggestions() {
  const ul = $("suggest");
  ul.replaceChildren();
  ul.hidden = !sugg.items.length;
  sugg.items.forEach((kw, n) => {
    const li = el("li", { class: n === sugg.active ? "active" : "", role: "option", text: kw });
    li.addEventListener("mousedown", (e) => { e.preventDefault(); acceptSuggestion(n); });
    ul.appendChild(li);
  });
  if (sugg.items.length) {
    ul.appendChild(el("li", { class: "hint", text: "↑↓ select · Enter/Tab add · Shift+Del forget" }));
  }
}

function hideSuggestions() {
  sugg.token++;
  sugg.items = [];
  sugg.active = -1;
  renderSuggestions();
}

function acceptSuggestion(n) {
  const kw = sugg.items[n];
  if (!kw) return;
  $("f-keyword").value = "";
  addKeyword(kw);
  $("f-keyword").focus();
  loadSuggestions();
}

async function forgetSuggestion(n) {
  const kw = sugg.items[n];
  if (!kw) return;
  try {
    await api("/api/keywords/forget", { keyword: kw });
    toast(`Forgot "${kw}"`);
  } catch (e) {
    toast("Could not forget keyword", true);
  }
  loadSuggestions();
}

// Returns true if the key was consumed by the suggestion list.
function suggestionKey(e) {
  const key = e.key;
  const open = sugg.items.length > 0;
  if (!open) return false;
  if (key === "ArrowDown" || key === "ArrowUp") {
    const n = sugg.items.length;
    sugg.active = key === "ArrowDown" ? (sugg.active + 1) % n : (sugg.active - 1 + n) % n;
    renderSuggestions();
    return true;
  }
  if ((key === "Enter" || (key === "Tab" && !e.shiftKey)) && sugg.active >= 0 && !e.ctrlKey) {
    acceptSuggestion(sugg.active);
    return true;
  }
  if (key === "Delete" && e.shiftKey && sugg.active >= 0) {
    forgetSuggestion(sugg.active);
    return true;
  }
  if (key === "Escape") {
    hideSuggestions();
    return true;
  }
  return false;
}

function addKeyword(raw) {
  const kw = raw.trim().replace(/;/g, ",");
  if (!kw) return false;
  if (!state.keywords.some((k) => k.toLowerCase() === kw.toLowerCase())) state.keywords.push(kw);
  state.touched = true;
  renderChips();
  return true;
}

// ---------- save / skip ----------
let busy = false;

async function save() {
  if (busy || state.idx < 0) return;
  const pending = $("f-keyword").value;
  if (pending.trim()) { addKeyword(pending); $("f-keyword").value = ""; }
  const fields = {
    description: $("f-description").value,
    keywords: state.keywords,
    comment: $("f-comment").value,
  };
  if (!fields.description && !fields.keywords.length && !fields.comment) {
    toast("Nothing to write. Use S to skip.", true);
    return;
  }
  busy = true;
  const i = state.idx;
  try {
    await api(`/api/images/${state.images[i].id}/save`, { fields });
    state.images[i].mark = "saved";
    updateRow(i);
    toast("Saved");
    advance(i);
  } catch (e) {
    toast("Save failed", true);
  } finally {
    busy = false;
  }
}

async function skip() {
  if (busy || state.idx < 0) return;
  busy = true;
  const i = state.idx;
  try {
    await api(`/api/images/${state.images[i].id}/skip`, {});
    state.images[i].mark = "skipped";
    updateRow(i);
    advance(i);
  } catch (e) {
    toast("Skip failed", true);
  } finally {
    busy = false;
  }
}

function advance(i) {
  document.activeElement && document.activeElement.blur();
  const n = nextUnmarked(i);
  if (n >= 0) go(n);
  else if (i + 1 < state.images.length) go(i + 1);
  else { updateProgress(); toast("Last image reached"); }
}

// ---------- help ----------
const SHORTCUTS = [
  ["1 – 9, 0", "Pick guess 1–10 (fills the fields)"],
  ["↑ / ↓", "Move guess selection"],
  ["N", "None of these / custom entry"],
  ["Enter", "Save & go to next image"],
  ["Ctrl+Enter", "Save (also while typing)"],
  ["S", "Skip image (writes nothing)"],
  ["→ / J,  ← / K", "Next / previous image"],
  ["Alt + ← / →", "Same, while typing"],
  ["E  or  /", "Edit fields (focus description)"],
  ["Keyword field", "↑↓ select suggestion, Enter/Tab add, Shift+Del forget"],
  ["Tab", "Move between fields"],
  ["Esc", "Leave field / close help"],
  ["R", "Reset fields to selected guess"],
  ["U", "Load existing tags into fields"],
  ["H", "Hide / show finished images"],
  ["?", "This help"],
];

function buildHelp() {
  const t = $("helptable");
  for (const [k, d] of SHORTCUTS) {
    t.appendChild(el("tr", {}, [
      el("td", {}, [el("kbd", { text: k })]),
      el("td", { text: d }),
    ]));
  }
}

function toggleHelp(show) {
  const h = $("help");
  h.hidden = show === undefined ? !h.hidden : !show;
}

// ---------- keyboard ----------
function typing() {
  const a = document.activeElement;
  return a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA");
}

document.addEventListener("keydown", (e) => {
  if (e.metaKey) return;
  const key = e.key;

  if (!$("help").hidden) {
    if (key === "Escape" || key === "?") { toggleHelp(false); e.preventDefault(); }
    return;
  }

  if (typing()) {
    const a = document.activeElement;
    if (a.id === "f-keyword" && suggestionKey(e)) { e.preventDefault(); return; }
    if (key === "Escape") { a.blur(); e.preventDefault(); return; }
    if (key === "Enter" && e.ctrlKey) { e.preventDefault(); save(); return; }
    if (e.altKey && key === "ArrowRight") { e.preventDefault(); go(state.idx + 1); return; }
    if (e.altKey && key === "ArrowLeft") { e.preventDefault(); go(state.idx - 1); return; }
    if (a.id === "f-keyword") {
      if (key === "Enter" || key === ",") {
        e.preventDefault();
        if (a.value.trim()) { addKeyword(a.value); a.value = ""; }
        else if (key === "Enter") save();
      } else if (key === "Backspace" && !a.value && state.keywords.length) {
        state.keywords.pop(); state.touched = true; renderChips();
      }
    } else if (key === "Enter" && a.tagName === "INPUT") {
      e.preventDefault(); save();
    }
    return;
  }

  if (key === "Enter" && e.ctrlKey) { e.preventDefault(); save(); return; }
  if (e.ctrlKey || e.altKey) return;
  const cands = state.detail ? state.detail.candidates : [];
  let handled = true;
  if (/^[1-9]$/.test(key)) selectCandidate(Number(key) - 1, true);
  else if (key === "0") selectCandidate(9, true);
  else if (key === "ArrowDown") selectCandidate(Math.min(cands.length - 1, Math.max(0, state.selected + 1)), true);
  else if (key === "ArrowUp") selectCandidate(Math.max(0, state.selected - 1), true);
  else if (key === "Enter") save();
  else if (key === "s" || key === "S") skip();
  else if (key === "ArrowRight" || key === "j" || key === "J") go(state.idx + 1);
  else if (key === "ArrowLeft" || key === "k" || key === "K") go(state.idx - 1);
  else if (key === "n" || key === "N") selectNone();
  else if (key === "e" || key === "E" || key === "/") $("f-description").focus();
  else if (key === "r" || key === "R") { if (state.selected >= 0) selectCandidate(state.selected, true); }
  else if (key === "u" || key === "U") useCurrent();
  else if (key === "h" || key === "H") toggleHide();
  else if (key === "?") toggleHelp(true);
  else handled = false;
  if (handled) e.preventDefault();
});

for (const id of ["f-description", "f-comment"]) {
  $(id).addEventListener("input", () => { state.touched = true; });
}
$("f-keyword").addEventListener("focus", loadSuggestions);
$("f-keyword").addEventListener("input", loadSuggestions);
$("f-keyword").addEventListener("blur", hideSuggestions);

function toggleHide() {
  state.hideDone = !state.hideDone;
  $("hidedone").checked = state.hideDone;
  state.images.forEach((_, i) => updateRow(i));
}

// ---------- init ----------
async function init() {
  buildHelp();
  $("save").addEventListener("click", save);
  $("skip").addEventListener("click", skip);
  $("nonebtn").addEventListener("click", selectNone);
  $("helpbtn").addEventListener("click", () => toggleHelp(true));
  $("help").addEventListener("click", (e) => { if (e.target === $("help")) toggleHelp(false); });
  $("hidedone").addEventListener("change", toggleHide);
  $("rescan").addEventListener("click", async () => {
    try { await api("/api/rescan", {}); await loadList(true); toast("Rescanned"); } catch (e) { toast("Rescan failed", true); }
  });
  pollModelStatus();
  await loadList(false);
}

async function loadList(keepCurrent) {
  const keepId = keepCurrent && state.idx >= 0 ? state.images[state.idx].id : null;
  let data;
  try { data = await api("/api/images"); } catch (e) { toast("Could not load images", true); return; }
  state.images = data.images;
  state.idx = -1;
  renderList();
  if (!state.images.length) {
    $("empty").hidden = false;
    $("empty").textContent = "No images found in the data folder.";
    return;
  }
  $("empty").hidden = true;
  let start = keepId ? state.images.findIndex((x) => x.id === keepId) : -1;
  if (start < 0) start = state.images.findIndex((x) => !x.mark);
  go(start < 0 ? 0 : start);
}

init();
