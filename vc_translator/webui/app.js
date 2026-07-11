/* VC Translator — Editorial Ink SPA */
"use strict";

const $ = (sel, el) => (el || document).querySelector(sel);
const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ---------- API (pywebview bridge, with demo stub for browser preview) ---------- */
let api = null;
let DEMO = false; // decided at boot: pywebview injects its API after page load

const demoLines = [
  { uid: 1, utt_id: 1, offset: "12:02", en: '"rotate B, they\'re all showing A"', ja: "B回れ、全員Aに見えてる", starred: false },
  { uid: 2, utt_id: 2, offset: "12:18", en: '"need drop, I\'m broke"', ja: "武器ちょうだい、金ない", starred: false },
  { uid: 3, utt_id: 3, offset: "12:34", en: '"they\'re saving, let\'s force up mid"', ja: "敵はエコ、ミッドから仕掛けよう", starred: false },
];

function demoApi() {
  const sess = { id: 1, started_at: "2026-07-03T21:04:00", profile: "learning", minutes: 38, lines: 64, stars: 5, reviewed: false, density: [3, 6, 4, 8, 2, 7, 5, 8, 3, 5], star_bins: [2, 5] };
  return {
    get_boot: async () => ({ profile: "learning", consented: true, status: { state: "live", started_at: Date.now() / 1000 - 754, latency: 1.2 }, due_count: 12, suggest_live: true, pipeline_labels: { input: "CABLE OUTPUT", mic: "QUADCAST", stt: "LARGE-V3 · GPU", llm: "GEMMA4" } }),
    check_setup: async () => ({ vbcable: true, mic: true, ollama: true, model: true, whisper: false, want_model: "gemma4:latest" }),
    loopback_test: async () => { setTimeout(() => app.onEvent({ type: "loopback_result", data: { ok: true, peak: 0.21 } }), 600); return { ok: true }; },
    accept_consent: async () => ({ ok: true }),
    adjust_overlay: async () => { setTimeout(() => app.onEvent({ type: "adjust_done", data: { x_offset: 12, y_offset: 180 } }), 700); return { ok: true }; },
    start_pipeline: async () => ({ ok: true }),
    stop_pipeline: async () => ({ ok: true, due_count: 12 }),
    get_status: async () => ({ state: "live", latency: 1.2 }),
    toggle_star: async () => ({ starred: true, due_count: 13 }),
    ja_to_en: async () => ({ ok: true, pairs: [['"I\'ll flank around"', "裏取りしてくる"]] }),
    save_suggestion: async () => ({ ok: true, due_count: 13 }),
    get_library: async () => ({ due_count: 12, days: [{ date: "2026-07-03", lines: 115, stars: 7, sessions: [sess, { ...sess, id: 2, started_at: "2026-07-03T20:12:00", minutes: 41, lines: 51, stars: 2, reviewed: true }] }] }),
    get_session: async () => ({ meta: sess, due_count: 12, frequent: [["rotate", 6], ["lurk", 3], ["peek", 3]], saved: [{ id: 1, en: "one shot", ja: "瀕死・あと一発" }], lines: [
      { id: 1, offset: "04:12", en: '"he\'s lit, one shot, push him"', ja: "敵は瀕死、ワンパンだから詰めろ", starred: true, missed: false, audio_path: "x", words: [["he's", 0.0, 0.3], ["lit", 0.3, 0.6], ["one", 0.7, 0.9], ["shot", 0.9, 1.2], ["push", 1.4, 1.7], ["him", 1.7, 2.0]] },
      { id: 2, offset: "07:48", en: '"jiggle peek mid, don\'t wide swing"', ja: "ミッドをジグルピーク、大きく出るな", starred: false, missed: true, audio_path: "x" },
      { id: 3, offset: "09:02", en: '"save your ults for next round"', ja: "ウルトは次ラウンドに温存して", starred: false, missed: false, audio_path: "x" }] }),
    play_line: async () => ({ ok: true }), play_pause: async () => ({ ok: true }),
    play_word: async () => ({ ok: true }),
    play_seek: async () => ({ ok: true }), play_stop: async () => ({ ok: true }),
    mark_reviewed: async () => ({ ok: true }),
    explain_line: async () => ({ ok: true, explanation: "意味: 敵が瀕死であと一撃で倒せる状態\n表現: one shot = 体力が残りわずか\n使う場面: 敵にダメージを入れて詰めさせたい時" }),
    delete_session: async () => ({ ok: true, due_count: 12 }),
    search_history: async q => ({ results: [{ id: 1, session_id: 1, ts: "2026-07-03T21:08:12", en: `"rotate B ${q}"`, ja: "B回れ", starred: false }] }),
    shadow_start: async () => ({ ok: true }),
    shadow_stop: async () => ({ ok: true, spoken: "jiggle pick mid don't white swing", score: 71, words: [{ w: "jiggle", ok: true }, { w: "peek", ok: false }, { w: "mid", ok: true }, { w: "don't", ok: true }, { w: "wide", ok: false }, { w: "swing", ok: true }] }),
    get_due_cards: async () => ({ cards: [{ card_id: 1, utt_id: 2, en: '"jiggle peek mid, don\'t wide swing"', masked: '"●●●● peek mid, don\'t ●●●● ●●●●"', ja: "ミッドをジグルピーク、大きく出るな", audio_path: "x", source: "今日 21:04 の試合", ts: "2026-07-03T21:11:48" }] }),
    answer_card: async () => ({ due_count: 11 }),
    play_card: async () => ({ ok: true }),
    get_settings: async () => ({ profile: "learning", version: "1.0.0", schema: [
      { section: "発話検出(VAD)", items: [
        { path: "vad.threshold", label: "発話判定のしきい値", desc: "誤検出が多ければ上げる、取りこぼしが多ければ下げる", type: "slider", min: 0.1, max: 0.9, step: 0.05, unit: "", fmt: 2 },
        { path: "vad.min_silence_ms", label: "発話を確定する無音の長さ", desc: "短い=速いが細切れ / 長い=まとまるが遅い", type: "slider", min: 200, max: 800, step: 50, unit: "ms" }] },
      { section: "音声認識(STT)", items: [
        { path: "stt.model", label: "Whisper モデル", desc: "初回選択時に自動ダウンロードされる", type: "select", options: ["large-v3", "base"] },
        { path: "history.save_audio", label: "音声クリップも保存", desc: "リスニング練習用。1試合あたり 20MB 程度", type: "toggle" }] },
    ], values: { "vad.threshold": 0.5, "vad.min_silence_ms": 400, "stt.model": "large-v3", "history.save_audio": true } }),
    set_setting: async () => ({ ok: true }),
    set_profile: async p => ({ ok: true, suggest_live: p !== "ranked", labels: {} }),
  };
}

/* ---------- state ---------- */
const S = {
  view: "live",           // live | library | review | flash | settings
  profile: "learning",
  pipeline: "idle",       // idle | loading | live
  loadingMsg: "",
  startedAt: null,
  latency: null,
  labels: { input: "—", mic: "—", stt: "—", llm: "—" },
  llmError: false, llmRecovering: false, inputLost: false,
  suggestLive: true,
  lines: [],              // live lines {uid, utt_id, offset, en, ja, starred}
  suggests: [],           // latest suggestion set [{en, ja, saved}]
  jaEnResult: null,
  dueCount: 0,
  library: null,
  libQuery: "", searchResults: null,
  session: null, sessionId: null, explanations: {},
  filter: "all", selected: null, playSpeed: 0.75, playing: false,
  shadow: null,           // {stage: idle|rec|scoring|done, result}
  cards: [], cardIdx: 0, revealed: false, cardSpeed: 0.75, playRatio: 0,
  settings: null, setSection: 0, setQuery: "",
  showSetup: false, setup: null, loopback: null, consentChecked: false,
  follow: true,   // U4: auto-scroll the live transcript unless the user scrolled up
};

/* ---------- event push from Python ---------- */
const app = {
  onEvent(ev) {
    const d = ev.data || {};
    switch (ev.type) {
      case "line":
        S.lines.push({ uid: d.uid, utt_id: d.utt_id, offset: d.offset, en: d.en, ja: "", starred: false, low_conf: !!d.low_conf });
        if (S.lines.length > 80) S.lines.shift();
        break;
      case "ja": {
        const row = S.lines.find(l => l.uid === d.uid);
        if (row) row.ja = d.ja;
        break;
      }
      case "suggest":
        S.suggests = (d.pairs || []).map(p => ({ en: p[0], ja: p[1], saved: false, uid: d.uid }));
        break;
      case "status":
        S.pipeline = d.state; S.latency = d.latency;
        S.startedAt = d.started_at;
        if (d.state !== "loading") S.loadingMsg = "";
        break;
      case "loading":
        S.pipeline = "loading"; S.loadingMsg = d.msg || "";
        break;
      case "latency": S.latency = d.latency; break;
      case "labels": Object.assign(S.labels, d); break;
      case "health":
        if (d.input) { S.inputLost = d.input === "lost"; if (d.input === "lost") showToast("音声入力が切断されました — 再接続を試みています"); else if (d.input === "reconnected") showToast("音声入力が復帰しました"); }
        if (d.llm) { S.llmError = d.llm === "down"; S.llmRecovering = d.llm === "recovering"; }
        break;
      case "line_starred": {  // U1: star-last hotkey feedback
        const row = S.lines.find(l => l.uid === d.uid);
        if (row) row.starred = d.starred;
        if (d.due_count != null) S.dueCount = d.due_count;
        showToast(d.starred ? "★ 直前の発言を保存しました" : "☆ 保存を解除しました", "info");
        break;
      }
      case "loopback_result":
        S.loopback = d; render(); return;
      case "adjust_done":
        showToast(`字幕位置を保存しました (x:${d.x_offset}, y:${d.y_offset})`, "info");
        if (S.view === "settings") { api.get_settings().then(r => { if (r && r.schema) { S.settings = r; render(); } }); }
        return;
      case "error": S.llmError = true; S.loadingMsg = ""; break;
      case "play_progress": S.playRatio = d.ratio; updateWave(); return;
      case "play_done": S.playing = false; S.playRatio = 0; updateWave(); return;
      case "refined": {
        const live = S.lines.find(l => l.uid === d.uid);            // P3 live upgrade
        if (live) { live.en = d.en; live.low_conf = false; }
        if (S.session && S.sessionId === d.session_id) {
          const line = S.session.lines.find(l => l.id === d.utt_id);
          if (line) { line.en = d.en; line.words = d.words; line.low_conf = false; }
        }
        break;
      }
    }
    render();
  },
};
window.app = app;

/* ---------- helpers ---------- */
function fmtClock() {
  if (!S.startedAt) return "LIVE 00:00";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - S.startedAt));
  return `LIVE ${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}
function dateLabel(iso) {
  const d = new Date(iso + "T00:00:00");
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const days = ["日", "月", "火", "水", "木", "金", "土"];
  const label = `${d.getMonth() + 1}月${d.getDate()}日(${days[d.getDay()]})`;
  return +d === +today ? label : label;
}
function barsHtml(density, starBins, max = 22) {
  if (!density || !density.length) return "";
  const peak = Math.max(...density, 1);
  return density.slice(0, 40).map((v, i) =>
    `<div class="${starBins && starBins.includes(i) ? "hot" : ""}" style="height:${Math.max(4, Math.round(v / peak * max))}px"></div>`).join("");
}
function copyText(t) {
  const ta = document.createElement("textarea");
  ta.value = t; document.body.appendChild(ta); ta.select();
  document.execCommand("copy"); ta.remove();
}

let toastTimer = null;
function showToast(msg, type = "error") {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    document.body.appendChild(el);
  }
  el.textContent = (type === "error" ? "⚠ " : "") + msg;
  el.classList.toggle("info", type !== "error");
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 4000);
}

// Wrap the bridge so a Python exception never becomes an unhandled rejection
// that wedges the UI: failures surface as a toast and resolve to {ok:false}.
function wrapApi(raw) {
  return new Proxy(raw, {
    get(target, prop) {
      const orig = target[prop];
      if (typeof orig !== "function") return orig;
      return (...args) => Promise.resolve()
        .then(() => orig.apply(target, args))
        .catch(err => {
          const msg = String((err && err.message) || err);
          console.error("api." + String(prop) + " failed:", err);
          showToast(msg);
          return { ok: false, error: msg };
        });
    },
  });
}

/* ---------- shell ---------- */
function renderShell() {
  document.querySelectorAll(".tab").forEach(b => {
    const v = b.dataset.view;
    b.classList.toggle("active", S.view === v || (v === "library" && S.view === "review"));
  });
  const clock = $("#live-clock");
  clock.classList.toggle("hidden", S.pipeline !== "live");
  $("#profile-btn").textContent = S.profile.toUpperCase() + " ▾";
  const btn = $("#start-btn");
  if (S.pipeline === "live") { btn.textContent = "■ 停止"; btn.className = "mono btn-white"; }
  else if (S.pipeline === "loading") { btn.textContent = "…"; btn.className = "mono btn-outline"; }
  else { btn.textContent = "▶ 開始"; btn.className = "mono btn-coral"; }
}
setInterval(() => { if (S.pipeline === "live") $("#clock-text") && ($("#clock-text").textContent = fmtClock()); }, 1000);

/* ---------- live view ---------- */
function renderLive() {
  const showRail = S.suggestLive;
  if (S.pipeline === "loading") {
    return `<div class="center-msg">${esc(S.loadingMsg || "LOADING…")}</div>`;
  }
  const llmTxt = S.llmError ? "UNREACHABLE" : S.llmRecovering ? "RECOVERING…" : esc(S.labels.llm);
  const statusBar = `
    <div id="statusbar">
      <span class="${S.inputLost ? "err" : ""}">INPUT: ${S.inputLost ? "LOST" : esc(S.labels.input)}</span><span>MIC: ${esc(S.labels.mic)}</span>
      <span>STT: ${esc(S.labels.stt)}</span>
      <span class="${S.llmError || S.llmRecovering ? "err" : ""}">LLM: ${llmTxt}</span>
      <span class="lat">遅延 <b>${S.latency != null ? S.latency + "s" : "—"}</b></span>
    </div>`;
  const n = S.lines.length;
  const rows = S.lines.slice(-30).map((l, i, arr) => {
    const idx = arr.length - 1 - i;
    const op = idx === 0 ? 1 : idx === 1 ? .7 : .45;
    return `
    <div class="t-row ${idx === 0 ? "latest" : ""} ${l.low_conf ? "lowconf" : ""}" style="opacity:${op}">
      <div class="t-ts">${esc(l.offset)}</div>
      <div class="t-main"><div class="t-en">${l.low_conf ? '<span class="lc-mark" title="認識の信頼度が低い">≈</span> ' : ""}${esc(l.en)}</div>${l.ja ? `<div class="t-ja">${esc(l.ja)}</div>` : ""}</div>
      ${l.utt_id ? `<div class="t-star ${l.starred ? "on" : ""}" data-star="${l.uid}">${l.starred ? "★" : "☆"}</div>` : ""}
    </div>`;
  }).join("");
  const recog = S.pipeline === "live"
    ? `<div class="t-recog"><div class="t-ts">…</div><div class="txt mono">認識中 <span class="cursor">▍</span></div></div>`
    : (n === 0 ? `<div class="center-msg">「▶ 開始」で翻訳を開始します</div>` : "");
  const rail = showRail ? `
    <div class="rail">
      <div class="rail-head">SUGGEST — こう返せる</div>
      ${S.suggests.map((sg, i) => `
        <div class="sug-card" data-sug="${i}">
          <div class="s-star ${sg.saved ? "on" : ""}" data-sugstar="${i}">${sg.saved ? "★" : "☆"}</div>
          <div class="s-en">${esc(sg.en)}</div><div class="s-ja">${esc(sg.ja)}</div>
        </div>`).join("")}
      <div class="rail-note">クリックで読み上げ表示<br>★でカード保存</div>
    </div>` : "";
  const jaEn = `
    <div class="ja-en-bar">
      <div class="lbl mono">JA→EN</div>
      <input id="ja-en-input" placeholder="言いたいことを日本語で…" value="">
      ${S.jaEnResult ? `<div class="ja-en-result" id="ja-en-copy" title="クリックでコピー">→ ${esc(S.jaEnResult)}</div>` : ""}
    </div>`;
  const followChip = S.follow ? "" : `<div class="follow-chip mono" id="follow-chip">↓ 最新へ</div>`;
  return `${statusBar}<div class="live-body"><div class="transcript">${rows}${recog}</div>${followChip}${rail}</div>${jaEn}`;
}

/* ---------- library ---------- */
function renderLibrary() {
  const lib = S.library;
  if (!lib) return `<div class="center-msg">LOADING…</div>`;
  if (!lib.days.length) {
    return `<div class="center-msg">まだセッションがありません — 「▶ 開始」で最初の試合を記録しましょう</div>`;
  }
  const d0 = lib.days[0];
  const head = `
    <div class="lib-head">
      <div style="flex:1">
        <div class="lib-date">${dateLabel(d0.date)}</div>
        <div class="lib-sum mono">${d0.sessions.length} SESSIONS · ${d0.lines} LINES · ${d0.stars} SAVED</div>
        <input class="lib-search" id="lib-search" placeholder="🔍 英語・日本語で検索…" value="${esc(S.libQuery)}">
      </div>
      ${lib.due_count > 0 ? `
      <div class="review-cta" id="go-flash">
        <div class="num">${lib.due_count}</div>
        <div style="flex:1"><div class="t1">今日の復習フレーズ</div><div class="t2">期限切れになる前に</div></div>
        <div class="go mono">START →</div>
      </div>` : ""}
    </div>`;
  if (S.searchResults) {
    const rs = S.searchResults;
    const body = rs.length ? rs.map(r => `
      <div class="sess-row" data-open-line="${r.session_id}">
        <div class="t-ts">${esc((r.ts || "").slice(5, 16).replace("T", " "))}</div>
        <div class="t-main"><div class="t-en">${esc(r.en)}</div><div class="t-ja">${esc(r.ja)}</div></div>
        ${r.starred ? '<div class="t-star on">★</div>' : ""}
      </div>`).join("")
      : `<div class="center-msg">「${esc(S.libQuery)}」に一致する発言はありません</div>`;
    return head + `<div class="lib-day-label">検索結果 ${rs.length} 件</div><div class="lib-list">${body}</div>`;
  }
  const list = lib.days.map((day, di) => {
    const label = di === 0 ? "" : `<div class="lib-day-label">${dateLabel(day.date)}</div>`;
    return label + day.sessions.map((s, si) => {
      const op = di === 0 ? (si === 0 ? 1 : .8) : .55;
      const t = s.started_at.slice(11, 16);
      return `
      <div class="sess-row" style="opacity:${op}" data-session="${s.id}">
        <div class="sess-time"><div class="h">${di === 0 ? t : s.started_at.slice(5, 10).replace("-", "/")}</div><div class="m">${s.minutes} MIN</div></div>
        <div class="bars">${barsHtml(s.density, s.star_bins)}</div>
        <div class="sess-meta">${s.lines} LINES · ★${s.stars}</div>
        <div class="badge ${s.reviewed ? "done" : "new"}">${s.reviewed ? "レビュー済" : "未レビュー"}</div>
        <div class="sess-del" data-del-session="${s.id}" title="このセッションを削除">🗑</div>
      </div>`;
    }).join("");
  }).join("");
  return head + `<div class="lib-list">${list}</div>`;
}

/* ---------- review ---------- */
function renderReview() {
  const d = S.session;
  if (!d) return `<div class="center-msg">LOADING…</div>`;
  const m = d.meta || {};
  const t = (m.started_at || "").slice(11, 16);
  const lines = d.lines.filter(l =>
    S.filter === "all" ? true : S.filter === "saved" ? l.starred : l.missed);
  const missedN = d.lines.filter(l => l.missed).length;
  const savedN = d.lines.filter(l => l.starred).length;
  const head = `
    <div class="rev-head">
      <div class="back-link" id="back-lib">← ライブラリ</div>
      <div class="rev-title">今日 ${t}</div>
      <div class="rev-sum mono">${m.minutes || "?"} MIN · ${d.lines.length} LINES · ${savedN} SAVED</div>
      <div class="bars">${barsHtml(m.density, m.star_bins)}</div>
      <div class="rev-del mono" id="del-session" title="このセッションを削除">🗑 削除</div>
    </div>`;
  const chips = `
    <div class="chips">
      <span class="chip ${S.filter === "all" ? "active" : ""}" data-filter="all">ALL ${d.lines.length}</span>
      <span class="chip ${S.filter === "saved" ? "active" : ""}" data-filter="saved">★ SAVED ${savedN}</span>
      <span class="chip ${S.filter === "missed" ? "active" : ""}" data-filter="missed">聞き逃し ${missedN}</span>
    </div>`;
  const rows = lines.map(l => {
    const sel = S.selected === l.id;
    const exp = S.explanations[l.id];
    const ctrl = sel ? `
      <div class="r-ctrl">
        <span class="play" data-play="${l.id}">${S.playing ? "❚❚" : "▶"} <span data-speed>${S.playSpeed}×</span></span>
        <span class="act" data-save="${l.id}">${l.starred ? "★ SAVED" : "＋ SAVE"}</span>
        <span class="act" data-shadow="${l.id}">🎙 SHADOW</span>
        <span class="act" data-explain="${l.id}">📖 解説</span>
      </div>${exp ? `<div class="r-explain">${esc(exp).replace(/\n/g, "<br>")}</div>` : ""}` : "";
    const enHtml = (sel && l.words && l.words.length)
      ? l.words.map(w => `<span class="wp" data-wordplay="${l.id}|${w[1]}|${w[2]}">${esc(w[0])}</span>`).join(" ")
      : `${l.low_conf ? '<span class="lc-mark" title="認識の信頼度が低い">≈</span> ' : ""}${esc(l.en)}`;
    return `
    <div class="r-row ${sel ? "sel" : ""} ${l.low_conf ? "lowconf" : ""}" data-line="${l.id}">
      <div class="t-ts">${esc(l.offset)}</div>
      <div class="t-main"><div class="t-en ${sel && l.words ? "words" : ""}">${enHtml}</div><div class="t-ja">${esc(l.ja)}</div>${ctrl}</div>
      <div class="t-star ${l.starred ? "on" : ""}" data-rstar="${l.id}">${l.starred ? "★" : "☆"}</div>
    </div>`;
  }).join("");
  const rail = S.shadow ? renderShadowPanel() : `
    <div class="rail">
      <div>
        <div class="rail-head">FREQUENT</div>
        <div class="freq-chips">${d.frequent.map(([w, n]) => `<div>${esc(w)} <b>×${n}</b></div>`).join("")}</div>
      </div>
      <div style="flex:1;margin-top:6px">
        <div class="rail-head">SAVED — ${d.saved.length}</div>
        ${d.saved.map(sv => `<div class="saved-item"><div class="e">${esc(sv.en)}</div><div class="j">${esc(sv.ja)}</div></div>`).join("")}
      </div>
      ${S.dueCount > 0 ? `<div class="review-go" id="go-flash">REVIEW ${S.dueCount} →</div>` : ""}
    </div>`;
  return head + `<div class="rev-body"><div class="rev-list">${chips}${rows}</div>${rail}</div>`;
}

function renderShadowPanel() {
  const sh = S.shadow;
  const line = S.session.lines.find(l => l.id === sh.id) || {};
  let body = "";
  if (sh.stage === "idle") {
    body = `
      <div class="shadow-btn btn-outline" data-play="${sh.id}">▶ お手本を再生</div>
      <div class="shadow-btn btn-coral" id="shadow-rec">● 録音してマネする</div>`;
  } else if (sh.stage === "rec") {
    body = `<div class="shadow-btn btn-white" id="shadow-stop">■ 停止して判定</div>
            <div class="rail-note">マイクに向かって読み上げてください</div>`;
  } else if (sh.stage === "scoring") {
    body = `<div class="rail-note mono">SCORING…</div>`;
  } else if (sh.stage === "done") {
    const r = sh.result;
    body = `
      <div class="score-big ${r.score >= 70 ? "good" : ""}">${r.score}%</div>
      <div class="shadow-words">${r.words.map(w => `<span class="${w.ok ? "" : "miss"}">${esc(w.w)}</span>`).join(" ")}</div>
      <div class="shadow-spoken">認識: ${esc(r.spoken || "—")}</div>
      <div class="shadow-btn btn-outline" id="shadow-rec">● もう一度</div>`;
  }
  return `
    <div class="rail shadow-panel">
      <div class="rail-head">SHADOW</div>
      <div class="shadow-target">${esc(line.en || "")}</div>
      ${body}
      <div class="shadow-btn" id="shadow-close" style="color:var(--min)">閉じる</div>
    </div>`;
}

/* ---------- flashcards ---------- */
function renderFlash() {
  if (!S.cards.length) {
    return `<div class="center-msg">復習するカードはありません 🎉 — レビュー画面で ★ 保存すると増えます</div>`;
  }
  if (S.cardIdx >= S.cards.length) {
    return `<div class="center-msg">今日の復習完了 — ${S.cards.length} 枚やりきりました 🎉</div>`;
  }
  const c = S.cards[S.cardIdx];
  const wave = Array.from({ length: 11 }, (_, i) =>
    `<div class="${i / 11 < S.playRatio ? "done" : ""}" style="height:${[8, 18, 12, 24, 10, 20, 14, 22, 9, 16, 11][i]}px"></div>`).join("");
  return `
    <div class="flash-progress"><div style="width:${(S.cardIdx / S.cards.length) * 100}%"></div></div>
    <div class="flash-body"><div class="flash-col">
      <div class="flash-instr">まず音声だけで聞き取ってみる</div>
      <div class="flash-card">
        <div class="flash-audio">
          <div class="play-circle" id="card-play">▶</div>
          <div class="wave" id="card-wave">${wave}</div>
          <div class="speed-btn mono" id="card-speed">${S.cardSpeed}× ▾</div>
        </div>
        <div class="flash-en ${S.revealed ? "revealed" : ""}">${esc(S.revealed ? c.en : c.masked)}</div>
        ${S.revealed ? `<div class="flash-ja">${esc(c.ja)}</div>`
          : `<div class="reveal-chip mono" id="card-reveal">タップして答えを表示</div>`}
      </div>
      <div class="flash-src">出典: ${esc(c.source)}${c.ts ? " · " + c.ts.slice(11, 16) : ""}</div>
      <div class="flash-answers mono">
        <button class="ng" data-answer="0">まだ怪しい</button>
        <button class="ok" data-answer="1">聞き取れた</button>
      </div>
    </div></div>`;
}
function updateWave() {
  const w = $("#card-wave");
  if (w) [...w.children].forEach((el, i) => el.classList.toggle("done", i / w.children.length < S.playRatio));
}

/* ---------- settings ---------- */
function renderSettings() {
  const st = S.settings;
  if (!st) return `<div class="center-msg">LOADING…</div>`;
  const q = S.setQuery.toLowerCase();
  const sections = st.schema;
  const sec = sections[S.setSection] || sections[0];
  const items = q
    ? sections.flatMap(s => s.items).filter(i => (i.label + i.desc).toLowerCase().includes(q))
    : (sec ? sec.items : []);
  const profilebar = `
    <div class="set-profilebar">
      <div class="lbl mono">PROFILE</div>
      <div class="segment">
        <button class="${st.profile === "learning" ? "active" : ""}" data-profile="learning">learning<small>精度重視</small></button>
        <button class="${st.profile === "ranked" ? "active" : ""}" data-profile="ranked">ranked<small>速度重視</small></button>
      </div>
      <input class="set-search" id="set-search" placeholder="🔍 設定を検索…" value="${esc(S.setQuery)}">
    </div>`;
  const nav = `
    <div class="set-nav">
      ${sections.map((s, i) => `<button class="${!q && i === S.setSection ? "active" : ""}" data-section="${i}">${esc(s.section)}</button>`).join("")}
    </div>`;
  const rows = items.map(item => {
    const v = st.values[item.path];
    let ctrl = "", val = "";
    if (item.type === "slider") {
      const fmt = item.fmt ? Number(v ?? item.min).toFixed(item.fmt) : String(v ?? item.min);
      const pct = ((v ?? item.min) - item.min) / (item.max - item.min) * 100;
      ctrl = `<input type="range" min="${item.min}" max="${item.max}" step="${item.step}" value="${v ?? item.min}" data-set="${item.path}"
        style="background:linear-gradient(to right,var(--coral) ${pct}%,var(--track) ${pct}%)">`;
      val = `<div class="set-val">${fmt}<small>${item.unit || ""}</small></div>`;
    } else if (item.type === "select") {
      ctrl = `<select class="set-select" data-set="${item.path}">
        ${item.options.map(o => `<option ${o === v ? "selected" : ""}>${o}</option>`).join("")}</select>`;
    } else if (item.type === "toggle") {
      ctrl = `<div class="toggle ${v ? "on" : ""}" data-toggle="${item.path}"></div>`;
    } else if (item.type === "button") {
      ctrl = `<button class="btn-outline set-action mono" data-action="${item.action}">${esc(item.button || item.label)}</button>`;
    } else {
      ctrl = `<input class="set-text" value="${esc(v ?? "")}" data-settext="${item.path}">`;
    }
    return `
    <div class="set-row">
      <div class="grow"><div class="name">${esc(item.label)}</div><div class="desc">${esc(item.desc)}</div></div>
      ${ctrl}${val}
    </div>`;
  }).join("");
  const footer = `
    <div class="set-footer">
      <div class="note">変更は即座に config.yaml へ保存されます</div>
      <div class="note ver">VC Translator v${esc(st.version || "?")}</div>
    </div>`;
  return profilebar + `<div class="set-body">${nav}<div class="set-list">${rows}${footer}</div></div>`;
}

/* ---------- setup / consent overlay ---------- */
function renderSetupOverlay() {
  if (!S.showSetup) return "";
  const s = S.setup;
  const consented = S.consented;
  const row = (ok, label, hint) => `
    <div class="setup-row">
      <span class="setup-ico ${ok === null ? "wait" : ok ? "ok" : "ng"}">${ok === null ? "…" : ok ? "✓" : "✕"}</span>
      <div><div class="setup-lbl">${esc(label)}</div>${hint ? `<div class="setup-hint">${hint}</div>` : ""}</div>
    </div>`;
  const checks = !s ? `<div class="center-msg">確認中…</div>` : `
    ${row(s.vbcable, "VB-Cable (CABLE Output)", s.vbcable ? "" : 'VB-Audio Virtual Cable を導入し再起動してください <a href="https://vb-audio.com/Cable/" target="_blank">配布ページ</a>')}
    ${row(s.mic, "マイク(練習用)", s.mic ? "" : "WASAPI マイクが見つかりません")}
    ${row(s.ollama, "Ollama サーバー", s.ollama ? "" : "Ollama を起動してください(自動起動も試みます)")}
    ${row(s.model, `翻訳モデル (${esc(s.want_model || "")})`, s.model ? "" : `ollama pull ${esc((s.want_model||"").split(":")[0])} を実行`)}
    ${row(s.whisper, "音声認識モデル", s.whisper ? "" : "初回の翻訳開始時に自動ダウンロードされます(数GB)")}`;
  const lb = S.loopback;
  const lbLine = lb == null ? "" :
    lb === "testing" ? `<div class="setup-hint">テスト中…(音が鳴ります)</div>` :
    lb.ok ? `<div class="setup-ok">✓ 音声ルーティング OK(peak ${lb.peak})</div>` :
    `<div class="setup-ng">✕ ${esc(lb.error || "音が検出できませんでした")}</div>`;
  return `
    <div class="setup-mask">
      <div class="setup-card">
        <div class="setup-title en">SETUP — セットアップ確認</div>
        ${checks}
        <div class="setup-test">
          <button class="mono btn-outline" id="loopback-btn">🔊 音声ルーティングをテスト</button>
          <button class="mono btn-outline" id="recheck-btn">🔄 再確認</button>
          ${lbLine}
        </div>
        ${!consented ? `
        <label class="setup-consent"><input type="checkbox" id="consent-cb" ${S.consentChecked ? "checked" : ""}>
          味方の音声を<b>この PC 内にのみ</b>記録・保存し、配信等で第三者の声を無断公開しないことに同意します</label>` : ""}
        <div class="setup-actions">
          ${!consented
            ? `<button class="mono btn-coral" id="consent-ok" ${S.consentChecked ? "" : "disabled"}>同意して始める</button>`
            : `<button class="mono btn-white" id="setup-close">閉じる</button>`}
        </div>
      </div>
    </div>`;
}

/* ---------- render root ---------- */
function render() {
  renderShell();
  const v = $("#view");
  const html =
    S.view === "live" ? renderLive() :
    S.view === "library" ? renderLibrary() :
    S.view === "review" ? renderReview() :
    S.view === "flash" ? renderFlash() : renderSettings();
  // preserve JA->EN input focus/value across re-renders
  const inp = $("#ja-en-input");
  const saved = inp && document.activeElement === inp ? { v: inp.value, s: inp.selectionStart } : null;
  const prevScroll = (() => { const el = $(".transcript"); return el ? el.scrollTop : null; })();
  v.innerHTML = html + renderSetupOverlay();
  if (saved) {
    const ni = $("#ja-en-input");
    if (ni) { ni.value = saved.v; ni.focus(); ni.setSelectionRange(saved.s, saved.s); }
  }
  const tr = $(".transcript");
  if (tr) tr.scrollTop = S.follow ? tr.scrollHeight : (prevScroll ?? tr.scrollHeight);  // U4
}

// U4: leaving the bottom pauses auto-follow; returning re-enables it
document.addEventListener("scroll", e => {
  if (!e.target.classList || !e.target.classList.contains("transcript")) return;
  const el = e.target;
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  if (S.follow !== atBottom) { S.follow = atBottom; render(); }
}, true);

async function openSetup() {
  S.showSetup = true; S.setup = null; S.loopback = null; render();
  const r = await api.check_setup();
  if (r) S.setup = r;
  render();
}

/* ---------- navigation / data loading ---------- */
async function goto(view) {
  S.view = view;
  if (view === "library") {
    S.searchResults = null; S.libQuery = "";
    const r = await api.get_library();
    if (r && r.days) { S.library = r; S.dueCount = r.due_count; }
  }
  if (view === "flash") {
    const r = await api.get_due_cards();
    if (r && r.cards) { S.cards = r.cards; S.cardIdx = 0; S.revealed = false; S.playRatio = 0; }
  }
  if (view === "settings") {
    const r = await api.get_settings();
    if (r && r.schema) S.settings = r;
  }
  render();
}

async function switchProfile(profile) {
  S.profile = profile;
  const r = await api.set_profile(profile);
  if (r && r.suggest_live !== undefined) S.suggestLive = r.suggest_live;
  if (r && r.labels) Object.assign(S.labels, r.labels);
  // settings are per-profile, so re-fetch the effective values for the new one
  if (S.view === "settings") {
    const gs = await api.get_settings();
    if (gs && gs.schema) S.settings = gs;
  }
}

async function openSession(id) {
  S.view = "review"; S.sessionId = id; S.session = null;
  S.filter = "all"; S.selected = null; S.shadow = null; S.explanations = {};
  render();
  const d = await api.get_session(id);
  if (!d || !d.lines) return;  // error already surfaced via toast
  S.session = d; S.dueCount = d.due_count;
  api.mark_reviewed(id);
  render();
}

/* ---------- event delegation ---------- */
document.addEventListener("click", async e => {
  const t = e.target.closest("[data-view],[data-star],[data-sug],[data-sugstar],#ja-en-copy,#go-flash,[data-session],[data-del-session],[data-open-line],#del-session,#back-lib,[data-filter],[data-wordplay],[data-line],[data-play],[data-save],[data-shadow],[data-explain],[data-rstar],#shadow-rec,#shadow-stop,#shadow-close,#card-play,#card-speed,#card-reveal,[data-answer],[data-profile],[data-section],[data-toggle],[data-action],#follow-chip,#setup-btn,#setup-close,#recheck-btn,#loopback-btn,#consent-ok,#start-btn,#profile-btn");
  if (!t) return;

  if (t.id === "follow-chip") { S.follow = true; render(); return; }  // U4

  /* setup / consent */
  if (t.id === "setup-btn") { openSetup(); return; }
  if (t.id === "setup-close") { S.showSetup = false; render(); return; }
  if (t.id === "recheck-btn") { S.setup = null; render(); S.setup = await api.check_setup(); render(); return; }
  if (t.id === "loopback-btn") {
    S.loopback = "testing"; render();
    const r = await api.loopback_test();
    if (r && r.ok === false && r.error) { S.loopback = { ok: false, error: r.error }; render(); }
    return;
  }
  if (t.id === "consent-ok") {
    await api.accept_consent(); S.consented = true; S.showSetup = false; render(); return;
  }

  if (t.id === "start-btn") {
    if (S.pipeline === "live") { const r = await api.stop_pipeline(); S.pipeline = "idle"; if (r && r.due_count != null) S.dueCount = r.due_count; }
    else if (S.pipeline === "idle") { S.pipeline = "loading"; S.loadingMsg = "PREPARING…"; await api.start_pipeline(S.profile); }
    render(); return;
  }
  if (t.id === "profile-btn") {
    if (S.pipeline !== "idle") return;
    await switchProfile(S.profile === "learning" ? "ranked" : "learning");
    render(); return;
  }
  if (t.dataset.view) { goto(t.dataset.view); return; }

  /* live */
  if (t.dataset.star) {
    const row = S.lines.find(l => l.uid == t.dataset.star);
    if (row && row.utt_id) {
      const r = await api.toggle_star(row.utt_id);
      row.starred = r.starred; S.dueCount = r.due_count; render();
    }
    return;
  }
  if (t.dataset.sugstar !== undefined) {
    e.stopPropagation();
    const sg = S.suggests[+t.dataset.sugstar];
    if (sg && !sg.saved) {
      const row = S.lines.find(l => l.uid === sg.uid);
      const r = await api.save_suggestion(row ? row.utt_id : null, sg.en, sg.ja);
      if (r && r.ok) { sg.saved = true; if (r.due_count != null) S.dueCount = r.due_count; render(); }
    }
    return;
  }
  if (t.dataset.sug !== undefined) {
    t.classList.toggle("big"); return;
  }
  if (t.id === "ja-en-copy") { copyText(S.jaEnResult); t.textContent = "✓ コピーしました"; return; }

  /* library */
  if (t.id === "go-flash") { goto("flash"); return; }
  if (t.dataset.delSession) {
    e.stopPropagation();
    if (confirm("このセッションの履歴と音声を削除しますか?")) {
      const r = await api.delete_session(+t.dataset.delSession);
      if (r && r.ok) { if (r.due_count != null) S.dueCount = r.due_count; await goto("library"); }
    }
    return;
  }
  if (t.dataset.openLine) { openSession(+t.dataset.openLine); return; }
  if (t.dataset.session) { openSession(+t.dataset.session); return; }

  /* review */
  if (t.id === "del-session") {
    if (confirm("このセッションの履歴と音声を削除しますか?")) {
      const r = await api.delete_session(S.sessionId);
      if (r && r.ok) { if (r.due_count != null) S.dueCount = r.due_count; await goto("library"); }
    }
    return;
  }
  if (t.id === "back-lib") { goto("library"); return; }
  if (t.dataset.wordplay) {
    e.stopPropagation();
    const [uid, start, end] = t.dataset.wordplay.split("|");
    await api.play_word(+uid, +start, +end);
    return;
  }
  if (t.dataset.filter) { S.filter = t.dataset.filter; S.selected = null; render(); return; }
  if (t.dataset.play) {
    e.stopPropagation();
    if (e.target.hasAttribute && e.target.hasAttribute("data-speed")) {
      S.playSpeed = S.playSpeed === 0.5 ? 0.75 : S.playSpeed === 0.75 ? 1 : 0.5;
      render(); return;
    }
    if (S.playing) { await api.play_pause(); S.playing = false; }
    else {
      const r = await api.play_line(+t.dataset.play, S.playSpeed);
      if (r && r.ok) {
        S.playing = true;
        const line = S.session && S.session.lines.find(l => l.id === +t.dataset.play);
        if (line && S.playSpeed <= 0.5) line.missed = true;
      }
    }
    render(); return;
  }
  if (t.dataset.save) {
    e.stopPropagation();
    const r = await api.toggle_star(+t.dataset.save);
    const line = S.session.lines.find(l => l.id === +t.dataset.save);
    if (line) line.starred = r.starred;
    S.dueCount = r.due_count;
    S.session.saved = S.session.lines.filter(l => l.starred).map(l => ({ id: l.id, en: l.en, ja: l.ja }));
    render(); return;
  }
  if (t.dataset.rstar) {
    e.stopPropagation();
    const r = await api.toggle_star(+t.dataset.rstar);
    const line = S.session.lines.find(l => l.id === +t.dataset.rstar);
    if (line) line.starred = r.starred;
    S.dueCount = r.due_count;
    S.session.saved = S.session.lines.filter(l => l.starred).map(l => ({ id: l.id, en: l.en, ja: l.ja }));
    render(); return;
  }
  if (t.dataset.explain) {
    e.stopPropagation();
    const id = +t.dataset.explain;
    if (S.explanations[id]) { delete S.explanations[id]; render(); return; }  // toggle off
    S.explanations[id] = "解説を生成中…"; render();
    const r = await api.explain_line(id);
    S.explanations[id] = (r && r.ok) ? r.explanation : "(解説の生成に失敗しました)";
    render(); return;
  }
  if (t.dataset.shadow) {
    e.stopPropagation();
    S.shadow = { id: +t.dataset.shadow, stage: "idle" }; render(); return;
  }
  if (t.id === "shadow-rec") {
    const r = await api.shadow_start(S.shadow.id);
    if (r.ok) { S.shadow.stage = "rec"; } else { alert(r.error); }
    render(); return;
  }
  if (t.id === "shadow-stop") {
    S.shadow.stage = "scoring"; render();
    const r = await api.shadow_stop();
    if (r.ok) { S.shadow.stage = "done"; S.shadow.result = r; }
    else { S.shadow.stage = "idle"; alert(r.error); }
    render(); return;
  }
  if (t.id === "shadow-close") { S.shadow = null; render(); return; }
  if (t.dataset.line) {
    const id = +t.dataset.line;
    S.selected = S.selected === id ? null : id;
    render(); return;
  }

  /* flashcards */
  if (t.id === "card-play") {
    const c = S.cards[S.cardIdx];
    await api.play_card(c.utt_id, S.cardSpeed); return;
  }
  if (t.id === "card-speed") {
    S.cardSpeed = S.cardSpeed === 0.5 ? 0.75 : S.cardSpeed === 0.75 ? 1 : 0.5;
    render(); return;
  }
  if (t.id === "card-reveal") { S.revealed = true; render(); return; }
  if (t.dataset.answer !== undefined) {
    const c = S.cards[S.cardIdx];
    const r = await api.answer_card(c.card_id, t.dataset.answer === "1");
    S.dueCount = r.due_count;
    S.cardIdx += 1; S.revealed = false; S.playRatio = 0;
    render(); return;
  }

  /* settings */
  if (t.dataset.profile) {
    await switchProfile(t.dataset.profile);
    render(); return;
  }
  if (t.dataset.action) {
    const r = await api[t.dataset.action]();
    if (r && r.ok === false && r.error) showToast(r.error);
    else if (t.dataset.action === "adjust_overlay") showToast("オーバーレイをドラッグして位置を決め、ダブルクリックで確定してください", "info");
    return;
  }
  if (t.dataset.section !== undefined) { S.setSection = +t.dataset.section; S.setQuery = ""; render(); return; }
  if (t.dataset.toggle) {
    const path = t.dataset.toggle;
    const nv = !S.settings.values[path];
    S.settings.values[path] = nv;
    await api.set_setting(path, nv); render(); return;
  }
});

let setDebounce = null;
let searchDebounce = null;
document.addEventListener("change", e => {
  if (e.target.id === "consent-cb") { S.consentChecked = e.target.checked; render(); }
});
document.addEventListener("input", e => {
  const el = e.target;
  if (el.id === "set-search") { S.setQuery = el.value; render(); const ni = $("#set-search"); ni.focus(); ni.setSelectionRange(ni.value.length, ni.value.length); return; }
  if (el.id === "lib-search") {
    S.libQuery = el.value;
    clearTimeout(searchDebounce);
    const q = el.value.trim();
    searchDebounce = setTimeout(async () => {
      if (!q) { S.searchResults = null; render(); return; }
      const r = await api.search_history(q);
      if (r && r.results) { S.searchResults = r.results; render();
        const ni = $("#lib-search"); if (ni) { ni.focus(); ni.setSelectionRange(ni.value.length, ni.value.length); } }
    }, 250);
    return;
  }
  const path = el.dataset.set || el.dataset.settext;
  if (!path) return;
  let v = el.value;
  if (el.type === "range") v = +v;
  S.settings.values[path] = v;
  // live-update the value readout + track fill without a full re-render
  if (el.type === "range") {
    const row = el.closest(".set-row");
    const valEl = row && row.querySelector(".set-val");
    const item = S.settings.schema.flatMap(s => s.items).find(i => i.path === path);
    if (valEl && item) {
      valEl.innerHTML = `${item.fmt ? (+v).toFixed(item.fmt) : v}<small>${item.unit || ""}</small>`;
      const pct = (v - item.min) / (item.max - item.min) * 100;
      el.style.background = `linear-gradient(to right,var(--coral) ${pct}%,var(--track) ${pct}%)`;
    }
  }
  clearTimeout(setDebounce);
  setDebounce = setTimeout(() => api.set_setting(path, v), 300);
});

document.addEventListener("keydown", async e => {
  if (e.key === "Enter" && e.target.id === "ja-en-input" && e.target.value.trim()) {
    const text = e.target.value.trim();
    e.target.value = "";
    S.jaEnResult = "…"; render();
    const r = await api.ja_to_en(text);
    S.jaEnResult = r.ok && r.pairs.length ? r.pairs[0][0] : "(変換失敗)";
    render();
  }
});

/* ---------- boot ---------- */
let booted = false;
async function boot() {
  if (booted) return;
  booted = true;
  DEMO = !window.pywebview;
  api = wrapApi(DEMO ? demoApi() : window.pywebview.api);
  window.addEventListener("unhandledrejection", ev => {
    console.error("unhandled:", ev.reason);
    showToast(String((ev.reason && ev.reason.message) || ev.reason));
  });
  const b = await api.get_boot();
  S.profile = b.profile;
  S.dueCount = b.due_count;
  S.suggestLive = b.suggest_live;
  Object.assign(S.labels, b.pipeline_labels || {});
  S.consented = !!b.consented;
  if (b.status) { S.pipeline = b.status.state; S.startedAt = b.status.started_at; S.latency = b.status.latency; }
  if (!S.consented) openSetup();  // first run: show setup + consent
  else if (S.dueCount > 0 && !DEMO) {  // U5: review reminder
    setTimeout(() => showToast(`復習が ${S.dueCount} 件たまっています — 「復習」タブからどうぞ`, "info"), 1200);
  }
  if (DEMO) { S.lines = demoLines; S.suggests = [
    { en: '"Sounds good, let\'s go"', ja: "いいね、行こう", saved: false, uid: 3 },
    { en: '"I\'ll smoke mid"', ja: "ミッドはスモーク焚く", saved: false, uid: 3 },
    { en: '"Careful, they might force"', ja: "フォースあるかも、注意", saved: false, uid: 3 }];
    S.jaEnResult = '"I\'ll flank around"'; }
  render();
}
window.addEventListener("pywebviewready", boot);
setTimeout(boot, 700);  // fallback: plain browser (demo) or late pywebview injection
