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
    eventCache: new Map(),
    lastKey: "",
    tab: "overview",
    sensorTab: null,
    coreTab: "nodes",
    platformTab: "summary",
  };
  let pending = null;
  let raf = 0;
  let stepBusy = false;

  const CAMPAIGN_NAMES = {
    A1: "感知数据篡改→通信拥塞",
    A2: "物理环境欺骗+控制误导",
    A3: "跨层隐蔽（雷达渐偏）",
    A4: "双因素仿冒",
    A5: "5G-A 信令风暴",
    A6: "切片/QoS 抢占",
    A7: "UPF/N6 流量劫持",
    A8: "NEF/订阅篡改",
    A9: "平台越权/云控注入",
    A10: "跨层隐蔽组合",
  };

  const TABS = [
    { id: "overview", label: "总览", groups: ["Scene", "Network", "Detection", "Defense"] },
    { id: "sensing", label: "感知", groups: ["Sensors"] },
    { id: "vehicles", label: "车辆", groups: ["Vehicles"] },
    { id: "core", label: "核心网", groups: ["Core"] },
    { id: "platform", label: "平台", groups: ["Platform"] },
    { id: "attacks", label: "攻击", groups: ["Attacks"] },
  ];
  const TAB_BY_GROUP = Object.fromEntries(
    TABS.flatMap((tab) => tab.groups.map((group) => [group, tab.id])),
  );
  const tabForTarget = (t) => TAB_BY_GROUP[t.group] || "overview";
  const targetMatches = (t, q) => !q
    || `${t.id} ${t.label} ${t.group}`.toLowerCase().includes(q);

  function tabTargets(tabId) {
    const tab = TABS.find((x) => x.id === tabId) || TABS[0];
    return ((state.snap && state.snap.targets) || [])
      .filter((t) => tab.groups.includes(t.group));
  }

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
    // 状态/时间这类轻量区域每次都更新；列表只在 cursor/step/state 真正变化时重绘。
    renderHeader();
    renderBanner();
    const step = snap.last_step || {};
    const key = `${snap.cursor}:${step.step_id || ""}:${snap.state || ""}:${snap.pause_reason || ""}`;
    if (key === state.lastKey) return;
    state.lastKey = key;
    renderTree();
    renderTabs();
    renderCards();
    renderEvents();
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
    const active = s.state === "running" || s.state === "paused";
    $("btnStart").disabled = !(s.state === "idle" || s.state === "stopped" || s.state === "error");
    $("btnStop").disabled = !active;
    $("btnInject").disabled = !active;
    $("btnInject").title = active ? "注入后自动暂停，点“下一步”走检查点" : "请先开始仿真";
    $("connInfo").textContent = s.enabled === false ? "追踪未启用" : "实时连接";
  }

  // ------------------------------------------------------------- tree
  function summaryOf(f) {
    return f && f.summary ? f.summary : "—";
  }

  function cardSelector(t) {
    if (t.id.startsWith("sensor:")) return '[data-card="sensor-group"]';
    if (t.id.startsWith("core:")) return '[data-card="core-group"]';
    if (t.id.startsWith("platform:")) return '[data-card="platform-group"]';
    return `[data-card="${CSS.escape(t.id)}"]`;
  }

  function focusTarget(tid) {
    const t = ((state.snap && state.snap.targets) || []).find((x) => x.id === tid);
    if (!t) return;
    state.selectedTarget = tid;
    state.tab = tabForTarget(t);
    if (t.id.startsWith("sensor:")) state.sensorTab = t.id;
    if (t.id.startsWith("core:")) {
      state.coreTab = ["core:ues", "core:sessions", "core:flows"].includes(t.id)
        ? t.id : "nodes";
    }
    if (t.id.startsWith("platform:")) {
      state.platformTab = t.id === "platform:summary" ? "summary" : t.id;
    }
    renderTabs();
    renderCards();
    renderTree();
    requestAnimationFrame(() => {
      const card = document.querySelector(cardSelector(t));
      if (card) card.scrollIntoView({ block: "center", behavior: "smooth" });
    });
  }

  function renderTree() {
    const s = state.snap || {};
    const q = state.filter.toLowerCase();
    const targets = (s.targets || []).filter((t) => targetMatches(t, q));
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
      el.onclick = () => focusTarget(el.dataset.target);
    });
  }

  // ------------------------------------------------------------ cards
  function colNames(target, rows) {
    if (target.fields && target.fields.length) return target.fields.map((f) => f.name);
    return rows && rows.length ? Object.keys(rows[0]).slice(0, 7) : [];
  }

  // 列对齐：数值列右对齐，标识符/文本列左对齐。
  // 只按“列”判定，避免同一列里数字和字符串混排时逐格跳动。
  const ID_COL = /(?:^|_)ids?$/i;   // object_id / session_id / teid 之类
  function isIdColumn(name) {
    return ID_COL.test(name) || /^teid$/i.test(name);
  }
  function numericColumn(rows, name) {
    if (isIdColumn(name)) return false;
    let seen = 0;
    for (const r of rows) {
      const v = r[name];
      if (v === null || v === undefined || v === "") continue;
      if (typeof v !== "number" || !Number.isFinite(v)) return false;
      seen += 1;
    }
    return seen > 0;
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
    const shown = rows.slice(0, 40);
    const numCols = new Set(cols.filter((c) => numericColumn(shown, c)));
    const clsOf = (c) => (numCols.has(c) ? "num" : "");
    const added = new Set(((f.delta && f.delta.added) || []).map(String));
    let h = `<table class="watch-table"><thead><tr>`;
    for (const c of cols) {
      const cls = clsOf(c);
      h += `<th${cls ? ` class="${cls}"` : ""}>${esc(c)}</th>`;
    }
    h += `</tr></thead><tbody>`;
    for (const r of shown) {
      const key = String(r[target.key_field || "id"] ?? "");
      const isNew = added.has(key);
      const isGhost = !!r.ghost;
      h += `<tr class="${isNew ? "new" : ""} ${isGhost ? "ghost" : ""}"
              data-raw="${esc(encodeURIComponent(JSON.stringify(r)))}">`;
      for (const c of cols) {
        const cls = clsOf(c);
        h += `<td${cls ? ` class="${cls}"` : ""}>${cell(r, c)}</td>`;
      }
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
      if (k === "health_status") {
        cls = v === "green" ? "good" : v === "red" ? "bad" : "changed";
      }
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

  function subTabsHTML(items, active, key) {
    if (items.length <= 1) return "";
    return `<div class="sub-tabs">` + items.map((it) => `
      <button type="button" class="sub-tab ${it.value === active ? "active" : ""}"
        data-subtab="${key}" data-value="${esc(it.value)}">${esc(it.label)}</button>
    `).join("") + `</div>`;
  }

  function mergedCardHTML(cardId, title, tag, summary, body) {
    return `<article class="card merged" data-card="${esc(cardId)}">
      <div class="card-head">
        <span class="ctitle">${esc(title)}</span>
        <span class="tag">${esc(tag)}</span>
        <span class="csum">${esc(summary)}</span>
      </div>
      <div class="card-body">${body}</div>
    </article>`;
  }

  function singleCardHTML(t) {
    const s = state.snap || {};
    const f = (s.frames || {})[t.id];
    const kindCls = t.group === "Attacks" ? "attack" : "";
    let body = `<div class="card-empty">等待数据…</div>`;
    if (f) {
      if (t.kind === "table") body = tableHTML(t, f);
      else if (t.kind === "event") body = eventHTML(t, f);
      else body = structHTML(t, f);
    }
    return `<article class="card ${kindCls}" data-card="${esc(t.id)}">
      <div class="card-head">
        <span class="ctitle">${esc(t.label)}</span>
        <span class="tag ${kindCls}">${esc(t.kind)}</span>
        <span class="csum">${esc(summaryOf(f))}</span>
      </div>
      <div class="card-body">${body}</div>
    </article>`;
  }

  function sensorCard(targets) {
    const s = state.snap || {};
    const list = targets.filter((t) => t.kind === "table");
    if (!list.length) return "";
    if (!list.some((t) => t.id === state.sensorTab)) state.sensorTab = list[0].id;
    const active = list.find((t) => t.id === state.sensorTab) || list[0];
    const f = (s.frames || {})[active.id];
    const items = list.map((t) => ({ value: t.id, label: t.label.split(" · ")[0] }));
    const body = subTabsHTML(items, active.id, "sensorTab")
      + `<div class="sub-body">${f ? tableHTML(active, f) : `<div class="card-empty">等待数据…</div>`}</div>`;
    return mergedCardHTML("sensor-group", "感知 · 目标列表", "sensors",
      `${list.length} 个传感器 · ${summaryOf(f)}`, body);
  }

  function nodeMetrics(v) {
    const skip = new Set(["entity", "health_status", "health_score",
      "health_reasons", "ues", "enabled"]);
    const preferred = ["attached", "registered", "sessions", "flows",
      "bus_pending", "queue_depth", "rejected", "received", "route",
      "active_sessions", "degraded_sessions", "unsubscribed", "revoked"];
    const keys = [];
    for (const k of preferred) {
      if (k in v && (v[k] === null || typeof v[k] !== "object")) keys.push(k);
      if (keys.length >= 4) break;
    }
    if (!keys.length) {
      for (const k of Object.keys(v)) {
        if (skip.has(k) || v[k] === null || typeof v[k] === "object") continue;
        keys.push(k);
        if (keys.length >= 3) break;
      }
    }
    return keys.map((k) => `<span><i>${esc(k)}</i>${esc(short(v[k]))}</span>`).join("");
  }

  function nodeChipHTML(t) {
    const s = state.snap || {};
    const f = (s.frames || {})[t.id];
    const v = (f && f.values) || {};
    const health = v.health_status || "unknown";
    const reasons = v.health_reasons ? ` title="${esc(v.health_reasons)}"` : "";
    const label = t.label.replace(" · node", "");
    return `<div class="node-chip ${esc(health)}" data-node="${esc(t.id)}"${reasons}>
      <div class="nc-head">
        <span class="hc-dot ${esc(health)}"></span>
        <b>${esc(label)}</b>
        <span class="nc-h">${esc(health)}</span>
      </div>
      <div class="nc-metrics">${nodeMetrics(v) || `<span class="muted">${esc(summaryOf(f))}</span>`}</div>
    </div>`;
  }

  function coreCard(targets) {
    const s = state.snap || {};
    const summaryT = targets.find((t) => t.id === "core:summary");
    const nodes = targets.filter((t) => t.id !== "core:summary"
      && t.kind === "struct" && t.id.startsWith("core:"));
    const tables = ["core:ues", "core:sessions", "core:flows"]
      .map((id) => targets.find((t) => t.id === id)).filter(Boolean);
    const tableLabels = { "core:ues": "UE", "core:sessions": "会话", "core:flows": "流表" };
    const items = [{ value: "nodes", label: "节点" }]
      .concat(tables.map((t) => ({ value: t.id, label: tableLabels[t.id] || t.label })));
    if (!items.some((it) => it.value === state.coreTab)) state.coreTab = "nodes";
    const summaryF = summaryT ? (s.frames || {})[summaryT.id] : null;

    let body = "";
    if (state.coreTab === "nodes") {
      body = (summaryT && summaryF ? structHTML(summaryT, summaryF) : "")
        + `<div class="node-grid">${nodes.map(nodeChipHTML).join("")}</div>`;
      if (!nodes.length && !summaryT) body = `<div class="card-empty">没有匹配的核心网节点</div>`;
    } else {
      const t = tables.find((x) => x.id === state.coreTab);
      const f = t ? (s.frames || {})[t.id] : null;
      body = t && f ? tableHTML(t, f) : `<div class="card-empty">等待数据…</div>`;
    }
    body = subTabsHTML(items, state.coreTab, "coreTab") + `<div class="sub-body">${body}</div>`;
    return mergedCardHTML("core-group", "核心网 · Core", "core",
      summaryF ? summaryOf(summaryF) : `${nodes.length} 个节点`, body);
  }

  function platformCard(targets) {
    const s = state.snap || {};
    const summaryT = targets.find((t) => t.id === "platform:summary");
    const tokenT = targets.find((t) => t.id === "platform:tokens");
    const subT = targets.find((t) => t.id === "platform:subscriptions");
    const items = [];
    if (summaryT) items.push({ value: "summary", label: "概览" });
    if (tokenT) items.push({ value: tokenT.id, label: "Token" });
    if (subT) items.push({ value: subT.id, label: "订阅" });
    if (!items.length) return "";
    if (!items.some((it) => it.value === state.platformTab)) {
      state.platformTab = items[0].value;
    }
    const summaryF = summaryT ? (s.frames || {})[summaryT.id] : null;
    let body;
    if (state.platformTab === "summary") {
      body = summaryT && summaryF ? structHTML(summaryT, summaryF)
        : `<div class="card-empty">等待数据…</div>`;
    } else {
      const t = state.platformTab === tokenT?.id ? tokenT : subT;
      const f = t ? (s.frames || {})[t.id] : null;
      body = t && f ? tableHTML(t, f) : `<div class="card-empty">等待数据…</div>`;
    }
    body = subTabsHTML(items, state.platformTab, "platformTab") + `<div class="sub-body">${body}</div>`;
    return mergedCardHTML("platform-group", "平台 · V2X", "platform",
      summaryF ? summaryOf(summaryF) : `${items.length} 个视图`, body);
  }

  function tabCount(tabId) {
    const n = tabTargets(tabId).length;
    if (!n) return 0;
    return ["sensing", "core", "platform"].includes(tabId) ? 1 : n;
  }

  function renderTabs() {
    let active = state.tab;
    if (!TABS.some((tab) => tab.id === active && tabCount(tab.id) > 0)) {
      const first = TABS.find((tab) => tabCount(tab.id) > 0);
      active = first ? first.id : TABS[0].id;
      state.tab = active;
    }
    $("cardTabs").innerHTML = TABS.map((tab) => {
      const n = tabCount(tab.id);
      return `<button type="button" class="card-tab ${active === tab.id ? "active" : ""}"
        data-tab="${tab.id}" ${n ? "" : "disabled"}>${tab.label}<span>${n}</span></button>`;
    }).join("");
    document.querySelectorAll("#cardTabs .card-tab").forEach((btn) => {
      btn.onclick = () => {
        state.tab = btn.dataset.tab;
        renderTabs();
        renderCards();
      };
    });
  }

  function renderCards() {
    const q = state.filter.toLowerCase();
    let targets = tabTargets(state.tab).filter((t) => targetMatches(t, q));
    if (!targets.length && q) {
      const alt = TABS.find((tab) => tabTargets(tab.id).some((t) => targetMatches(t, q)));
      if (alt) {
        state.tab = alt.id;
        renderTabs();
        targets = tabTargets(state.tab).filter((t) => targetMatches(t, q));
      }
    }
    targets = targets.slice().sort((a, b) => (a.order || 0) - (b.order || 0));
    const cards = [];
    if (state.tab === "sensing") {
      const card = sensorCard(targets);
      if (card) cards.push(card);
    } else if (state.tab === "core") {
      const card = coreCard(targets);
      if (card) cards.push(card);
    } else if (state.tab === "platform") {
      const card = platformCard(targets);
      if (card) cards.push(card);
    } else {
      for (const t of targets) cards.push(singleCardHTML(t));
    }
    $("cards").innerHTML = cards.join("")
      || `<p class="muted">该分组暂无匹配 target；可用左侧搜索或切换标签。</p>`;
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
    // 缓存最近事件，避免选中项被快照窗口挤掉后 Detail 失效。
    for (const e of events) state.eventCache.set(e.seq, e);
    if (state.eventCache.size > 300) {
      const drop = state.eventCache.size - 300;
      let i = 0;
      for (const k of state.eventCache.keys()) {
        if (i++ >= drop) break;
        state.eventCache.delete(k);
      }
    }
    $("traceEvents").innerHTML = `<div class="event-list">` + events.map((e) => {
      const cls = e.kind.startsWith("attack") ? "attack"
        : e.kind === "alert" ? "alert"
        : e.kind.includes("speed") || e.kind.includes("rise") ? "effect" : "";
      const active = state.selectedEvent === e.seq ? "active" : "";
      const forged = e.data && e.data.forged ? " forged" : "";
      return `<div class="event-item ${cls} ${active}${forged}" data-seq="${e.seq}" tabindex="0">
        <span class="et">${esc(fmt(e.t, 2))}s</span>
        <span class="ek">${esc(e.kind)}</span>
        <span class="es">${esc(e.summary)}</span>
      </div>`;
    }).join("") + `</div>`;
  }

  function selectEvent(seq) {
    const ev = (state.snap && (state.snap.events || []).find((x) => x.seq === seq))
            || state.eventCache.get(seq);
    if (!ev) {
      toast("该事件已超出缓存范围，请重新选择");
      return;
    }
    state.selectedEvent = seq;
    state.eventCache.set(seq, ev);
    showDetail(ev);
    // 只切换 active，不重建列表
    document.querySelectorAll("#traceEvents .event-item.active")
      .forEach((el) => el.classList.remove("active"));
    const item = document.querySelector(`#traceEvents .event-item[data-seq="${seq}"]`);
    if (item) item.classList.add("active");
  }

  function showDetail(obj) {
    const box = $("eventDetail");
    box.textContent = JSON.stringify(obj, null, 2);
    box.scrollTop = 0;
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

  // 稳定容器上的事件委托：列表重绘不会丢掉点击处理器。
  $("traceEvents").addEventListener("pointerdown", (e) => {
    const item = e.target.closest && e.target.closest(".event-item");
    if (!item) return;
    selectEvent(Number(item.dataset.seq));
  });
  $("traceEvents").addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const item = e.target.closest && e.target.closest(".event-item");
    if (!item) return;
    e.preventDefault();
    selectEvent(Number(item.dataset.seq));
  });
  $("cards").addEventListener("click", (e) => {
    const btn = e.target.closest && e.target.closest("[data-subtab]");
    if (!btn) return;
    state[btn.dataset.subtab] = btn.dataset.value;
    renderCards();
  });
  $("cards").addEventListener("pointerdown", (e) => {
    const tr = e.target.closest && e.target.closest("tr[data-raw]");
    if (!tr) return;
    try {
      showDetail(JSON.parse(decodeURIComponent(tr.dataset.raw)));
    } catch (err) { /* ignore */ }
  });

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

  async function startSession() {
    const duration = Math.max(5, Math.min(600,
      Number((state.snap && state.snap.duration_s) || 60)));
    const res = await post("/api/session/start", {
      seed: 11, duration, speed: 1, scene: "arterial",
    });
    if (res) {
      toast(`仿真已启动（${duration}s）；点“冻结”后注入攻击`);
      setTimeout(refresh, 300);
    }
  }

  async function stopSession() {
    const res = await post("/api/session/stop");
    if (res) {
      toast("仿真已停止");
      setTimeout(refresh, 300);
    }
  }

  function updateAttackHint() {
    const type = $("attackType").value;
    $("attackHint").textContent = CAMPAIGN_NAMES[type] || "";
    $("amplitudeOut").textContent = Number($("attackAmplitude").value).toFixed(2);
  }

  async function injectAttack() {
    const s = state.snap || {};
    if (!(s.state === "running" || s.state === "paused")) {
      toast("请先点“开始”启动仿真");
      return;
    }
    const wasRunning = s.state === "running";
    if (wasRunning) {
      const paused = await post("/api/session/pause");
      if (!paused) return;
    }
    const type = $("attackType").value;
    const amplitude = Number($("attackAmplitude").value);
    const duration = Math.max(1, Math.min(60,
      Number($("attackDuration").value) || 10));
    const res = await post("/api/attacks", {
      type, count: 1, amplitude, duration, start: null,
    });
    if (res) {
      toast(`${wasRunning ? "已暂停并注入" : "已注入"} ${type}（${amplitude.toFixed(2)} / ${duration}s）；点“下一步”从检查点开始`);
      setTimeout(refresh, 250);
    }
  }

  $("btnStart").onclick = startSession;
  $("btnStop").onclick = stopSession;
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
  $("btnInject").onclick = injectAttack;
  $("attackType").onchange = updateAttackHint;
  $("attackAmplitude").oninput = updateAttackHint;
  updateAttackHint();

  $("searchInput").oninput = (e) => {
    state.filter = e.target.value.trim();
    renderTree();
    renderTabs();
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
