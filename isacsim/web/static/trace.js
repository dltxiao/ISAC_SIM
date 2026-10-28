/* ISAC-SIM · 运行追踪页：SSE + watch cards + step controls */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    snap: null,
    selectedEvent: null,
    selectedTarget: null,
    filter: "",
    follow: true,
  };
  let pending = null;
  let raf = 0;
  let stepBusy = false;

  const CAMPAIGN_NAMES = {
    A1: "感知篡改→通信拥塞", A2: "物理欺骗+控制误导",
    A3: "跨层隐蔽", A4: "双因素仿冒",
  };

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
  const fmt = (v, n = 2) => {
    if (v === null || v === undefined || v === "") return "—";
    if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(n);
    return String(v);
  };
  const raw = (v) => {
    if (v === null || v === undefined) return "—";
    if (typeof v === "object") return JSON.stringify(v, null, 2);
    return String(v);
  };
  const short = (v) => {
    if (v === null || v === undefined) return "—";
    if (typeof v === "object") return JSON.stringify(v);
    return String(v);
  };

  async function api(path, method = "GET", body = null) {
    const opts = { method, headers: {} };
    if (body !== null) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    let data = null;
    try { data = await res.json(); } catch (e) { /* ignore */ }
    if (!res.ok) {
      const detail = (data && (data.detail || data.error)) || res.statusText;
      throw new Error(detail);
    }
    return data;
  }

  function toast(msg) {
    const el = $("toast");
    el.textContent = msg;
    el.style.display = "block";
    clearTimeout(el._timer);
    el._timer = setTimeout(() => { el.style.display = "none"; }, 3200);
  }

  function schedule(snap) {
    pending = snap;
    if (!raf) {
      raf = requestAnimationFrame(() => {
        raf = 0;
        if (pending) apply(pending);
        pending = null;
      });
    }
  }

  function apply(snap) {
    state.snap = snap;
    renderHeader();
    renderTree();
    renderCards();
    renderEvents();
    renderBanner();
    renderTimeline();
  }

  // ------------------------------------------------------------ header
  function renderHeader() {
    const s = state.snap || {};
    const labels = {
      idle: ["未启动", "idle"], running: ["运行中", "running"],
      paused: ["已暂停", "paused"], stopped: ["已停止", "idle"],
      starting: ["启动中", "starting"], error: ["错误", "error"],
    };
    const [text, cls] = labels[s.state] || [s.state || "未知", "idle"];
    const badge = $("stateBadge");
    badge.textContent = text;
    badge.className = `badge ${cls}`;
    $("simTime").textContent = `${fmt(s.t, 2)} s`;
    $("tickNo").textContent = fmt(s.tick, 0);
    $("cursorNo").textContent = fmt(s.cursor, 0);
    $("modeLabel").textContent = s.step_mode || (s.state === "paused" ? "paused" : "live");
    const run = s.attacks && Object.keys(s.attacks).length
      ? `run: ${Object.keys(s.attacks).join(", ")}` : "无活动攻击";
    $("runInfo").textContent = `${run} · ${s.pause_reason || "运行中"}`;
    $("btnStep").disabled = stepBusy || !(s.state === "paused" || s.state === "running");
    $("btnTick").disabled = $("btnStep").disabled;
    $("btnPause").disabled = s.state !== "running";
    $("btnResume").disabled = s.state !== "paused";
    $("connInfo").textContent = s.enabled === false ? "追踪未启用" : "实时连接";
  }

  // ------------------------------------------------------------- tree
  function summaryOf(f) {
    return f && f.summary ? f.summary : "—";
  }

  function renderTree() {
    const s = state.snap || {};
    const targets = (s.targets || []).filter((t) => {
      if (!state.filter) return true;
      const q = state.filter.toLowerCase();
      return `${t.id} ${t.label} ${t.group}`.toLowerCase().includes(q);
    });
    $("watchCount").textContent = String(targets.length);
    const groups = {};
    for (const t of targets) (groups[t.group] = groups[t.group] || []).push(t);
    let html = "";
    for (const [g, items] of Object.entries(groups)) {
      html += `<div class="watch-group">${esc(g)}</div>`;
      for (const t of items) {
        const f = (s.frames || {})[t.id];
        const dotCls = f && f.status === "changed" ? "changed" : "";
        const att = (t.group === "Attacks") ? "attack" : "";
        const active = state.selectedTarget === t.id ? "active" : "";
        html += `<div class="watch-item ${active}" data-target="${esc(t.id)}" title="${esc(t.description || t.id)}">
          <span class="dot ${dotCls} ${att}"></span>
          <span class="wid">${esc(t.id)}</span>
          <span class="wsum">${esc(summaryOf(f))}</span>
        </div>`;
      }
    }
    $("watchTree").innerHTML = html || `<p class="muted small">没有匹配的 target</p>`;
    $("watchTree").querySelectorAll(".watch-item").forEach((el) => {
      el.onclick = () => {
        state.selectedTarget = el.dataset.target;
        renderTree();
        const card = document.querySelector(`[data-card="${CSS.escape(el.dataset.target)}"]`);
        if (card) card.scrollIntoView({ block: "center", behavior: "smooth" });
      };
    });
  }

  // ------------------------------------------------------------ cards
  function colNames(target, rows) {
    if (target.fields && target.fields.length) return target.fields.map((f) => f.name);
    return rows && rows.length ? Object.keys(rows[0]).slice(0, 7) : [];
  }

  function cell(row, name) {
    if (name === "ghost") return row[name] ? `<span class="flag">GHOST</span>` : "—";
    if (name === "affected") return row[name] ? `<span class="flag warn">AFFECTED</span>` : "—";
    if (name === "flags") return row[name] ? `<span class="flag warn">${esc(row[name])}</span>` : "—";
    const v = row[name];
    if (typeof v === "number") return fmt(v, 2);
    return esc(short(v));
  }

  function tableHTML(target, f) {
    const rows = (f.values && f.values.rows) || [];
    if (!rows.length) return `<div class="card-empty">尚无数据</div>`;
    const cols = colNames(target, rows);
    const added = new Set(((f.delta && f.delta.added) || []).map(String));
    let h = `<table class="watch-table"><thead><tr>`;
    for (const c of cols) h += `<th>${esc(c)}</th>`;
    h += `</tr></thead><tbody>`;
    for (const r of rows.slice(0, 40)) {
      const key = String(r[target.key_field || "id"] ?? "");
      const isNew = added.has(key);
      const isGhost = !!r.ghost;
      h += `<tr class="${isNew ? "new" : ""} ${isGhost ? "ghost" : ""}"
              data-raw="${esc(encodeURIComponent(JSON.stringify(r)))}">`;
      for (const c of cols) h += `<td class="${typeof r[c] === "number" ? "num" : ""}">${cell(r, c)}</td>`;
      h += `</tr>`;
    }
    h += `</tbody></table>`;
    if (rows.length > 40) h += `<div class="muted small">仅显示 40/${rows.length} 行</div>`;
    return h;
  }

  function structHTML(target, f) {
    const vals = (f.values && (f.values.values || f.values)) || {};
    if (!vals || typeof vals !== "object") return `<div class="card-empty">${esc(short(vals))}</div>`;
    const changed = new Set(((f.delta && f.delta.changed) || []).map((c) => c.key || c));
    let h = `<div class="kv">`;
    for (const [k, v] of Object.entries(vals)) {
      if (k === "rows" || k === "alerts") continue;
      let cls = changed.has(k) ? "changed" : "";
      if (k === "finished" && v) cls = "bad";
      if (k === "affected" && v) cls = "bad";
      h += `<span class="k">${esc(k)}</span><span class="v ${cls}">${esc(raw(v))}</span>`;
    }
    h += `</div>`;
    return h;
  }

  function eventHTML(target, f) {
    const alerts = (f.values && f.values.alerts) || [];
    if (!alerts.length) return `<div class="card-empty">尚无告警</div>`;
    let h = `<div class="kv">`;
    for (const a of alerts.slice(-8).reverse()) {
      h += `<span class="k">${esc(fmt(a.t, 2))}s</span>
            <span class="v bad">${esc(a.rule)} @ ${esc(a.entity)}</span>`;
    }
    h += `</div>`;
    return h;
  }

  function renderCards() {
    const s = state.snap || {};
    const targets = (s.targets || []).slice().sort((a, b) => (a.order || 0) - (b.order || 0));
    const filter = state.filter.toLowerCase();
    const cards = [];
    for (const t of targets) {
      if (filter && !`${t.id} ${t.label} ${t.group}`.toLowerCase().includes(filter)) continue;
      const f = (s.frames || {})[t.id];
      const status = f ? f.status : "empty";
      const kindCls = t.group === "Attacks" ? "attack" : "";
      let body = `<div class="card-empty">等待数据…</div>`;
      if (f) {
        if (t.kind === "table") body = tableHTML(t, f);
        else if (t.kind === "event") body = eventHTML(t, f);
        else body = structHTML(t, f);
      }
      cards.push(`<article class="card ${kindCls}" data-card="${esc(t.id)}">
        <div class="card-head">
          <span class="ctitle">${esc(t.label)}</span>
          <span class="tag ${kindCls}">${esc(t.kind)}</span>
          <span class="csum">${esc(summaryOf(f))}</span>
        </div>
        <div class="card-body">${body}</div>
      </article>`);
    }
    $("cards").innerHTML = cards.join("") || `<p class="muted">暂无 target</p>`;
    $("cards").querySelectorAll("tr[data-raw]").forEach((tr) => {
      tr.onclick = () => {
        try { showDetail(JSON.parse(decodeURIComponent(tr.dataset.raw))); } catch (e) { /* ignore */ }
      };
    });
  }

  // ----------------------------------------------------------- events
  function renderEvents() {
    const s = state.snap || {};
    const events = (s.events || []).slice(-50).reverse();
    $("eventCount").textContent = String((s.events || []).length);
    if (!events.length) {
      $("traceEvents").innerHTML = `<p class="muted small">暂无事件</p>`;
      return;
    }
    $("traceEvents").innerHTML = `<div class="event-list">` + events.map((e) => {
      const cls = e.kind.startsWith("attack") ? "attack"
        : e.kind === "alert" ? "alert"
        : e.kind.includes("speed") || e.kind.includes("rise") ? "effect" : "";
      const active = state.selectedEvent === e.seq ? "active" : "";
      return `<div class="event-item ${cls} ${active}" data-seq="${e.seq}">
        <span class="et">${esc(fmt(e.t, 2))}s</span>
        <span class="ek">${esc(e.kind)}</span>
        <span class="es">${esc(e.summary)}</span>
      </div>`;
    }).join("") + `</div>`;
    $("traceEvents").querySelectorAll(".event-item").forEach((el) => {
      el.onclick = () => {
        state.selectedEvent = Number(el.dataset.seq);
        const ev = (state.snap.events || []).find((x) => x.seq === state.selectedEvent);
        if (ev) showDetail(ev);
        renderEvents();
      };
    });
  }

  function showDetail(obj) {
    $("eventDetail").textContent = JSON.stringify(obj, null, 2);
  }

  // ----------------------------------------------------------- banner
  function renderBanner() {
    const s = state.snap || {};
    const step = s.last_step;
    const b = $("stepBanner");
    if (step) {
      const from = step.from || {};
      const to = step.to || {};
      b.innerHTML = `
        <div class="step-title">${esc(step.title || step.reason)}
          <span class="step-reason">${esc(step.reason)} · ${esc(fmt(from.t, 2))}s → ${esc(fmt(to.t, 2))}s</span>
        </div>
        <div class="step-sub">${esc(step.narrative || "—")}</div>`;
    } else if (s.state === "paused") {
      b.innerHTML = `<div class="step-title">已暂停</div>
        <div class="step-sub muted">${esc(s.pause_reason || "manual")} · 点“下一步”推进到下一个检查点</div>`;
    } else {
      b.innerHTML = `<div class="step-title">连续运行</div>
        <div class="step-sub muted">注入攻击后，点“下一步”会在检查点暂停。</div>`;
    }
  }

  function renderTimeline() {
    const s = state.snap || {};
    const dur = Number(s.duration_s || 60);
    const t = Number(s.t || 0);
    $("timelineFill").style.width = `${Math.max(0, Math.min(100, (t / dur) * 100))}%`;
    const marks = [];
    for (const [aid, a] of Object.entries(s.attacks || {})) {
      const t0 = Number(a.t0 || 0);
      const t1 = Number(a.t1 || t0);
      if (t0 > 0) marks.push(`<span class="timeline-mark attack" style="left:${(t0 / dur) * 100}%"
        title="${esc(aid)} start ${t0}s"></span>`);
      if (t1 > t0) marks.push(`<span class="timeline-mark attack" style="left:${(t1 / dur) * 100}%"
        title="${esc(aid)} end ${t1}s"></span>`);
    }
    for (const e of (s.events || [])) {
      if (e.kind === "alert") {
        marks.push(`<span class="timeline-mark alert" style="left:${(Number(e.t || 0) / dur) * 100}%"
          title="${esc(e.summary)}"></span>`);
      }
    }
    $("timelineMarks").innerHTML = marks.join("");
  }

  // ---------------------------------------------------------- controls
  async function post(path, body = {}) {
    try {
      return await api(path, "POST", body);
    } catch (err) {
      toast(err.message);
      return null;
    }
  }

  async function refresh() {
    try {
      const snap = await api("/api/trace/state");
      schedule(snap);
    } catch (e) { /* ignore */ }
  }

  $("btnPause").onclick = async () => { await post("/api/session/pause"); refresh(); };
  $("btnResume").onclick = async () => { await post("/api/session/resume"); refresh(); };
  async function doStep(mode) {
    if (stepBusy) return;
    stepBusy = true;
    renderHeader();
    try {
      const res = await post("/api/trace/step", { mode, count: 1 });
      if (res && res.trace) schedule(res.trace);
      else await refresh();
    } finally {
      stepBusy = false;
      renderHeader();
    }
  }
  $("btnStep").onclick = () => doStep("checkpoint");
  $("btnTick").onclick = () => doStep("tick");
  $("btnInjectA1").onclick = async () => {
    const res = await post("/api/attacks", {
      type: "A1", count: 1, amplitude: 0.6, duration: 10, start: null,
    });
    if (res) {
      toast("已注入 A1（0.6 / 10s）；冻结后点“下一步”");
      setTimeout(refresh, 250);
    }
  };

  $("searchInput").oninput = (e) => {
    state.filter = e.target.value.trim();
    renderTree();
    renderCards();
  };

  document.addEventListener("keydown", (e) => {
    const tag = (e.target && e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea") return;
    if (e.code === "Space") { e.preventDefault(); $("btnStep").click(); }
    if (e.key.toLowerCase() === "f") {
      const s = state.snap || {};
      (s.state === "paused" ? $("btnResume") : $("btnPause")).click();
    }
  });

  // -------------------------------------------------------------- SSE
  function connect() {
    if (!window.EventSource) {
      setInterval(refresh, 250);
      return;
    }
    const es = new EventSource("/api/trace/stream");
    es.addEventListener("trace", (ev) => {
      try { schedule(JSON.parse(ev.data)); } catch (e) { /* ignore */ }
    });
    es.onopen = () => { $("connInfo").textContent = "实时连接 · 10 Hz"; };
    es.onerror = () => { $("connInfo").textContent = "连接中断，正在重连…"; };
  }

  refresh();
  connect();
})();
