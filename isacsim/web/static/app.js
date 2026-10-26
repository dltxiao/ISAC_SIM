/* ISAC-SIM 控制台前端：Canvas 渲染 + SSE 状态 + REST 控制 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const canvas = $("map");
  const ctx = canvas.getContext("2d");

  let state = null;
  let net = null;
  let netRequested = false;
  let transform = { scale: 1, ox: 0, oy: 0, xmin: 0, ymax: 0 };
  let selected = null;
  let hover = null;
  let logKeys = new Set();

  const CAMPAIGN_NAMES = {
    A1: "感知篡改→通信拥塞",
    A2: "物理欺骗+控制误导",
    A3: "跨层隐蔽",
    A4: "双因素仿冒",
  };
  const CAMPAIGN_COLORS = { A1: "#e05a5a", A2: "#f0a93b", A3: "#b07ae0", A4: "#3fc8e0" };

  // ---------------------------------------------------------------- utils
  const fmtInt = (n) => (n || 0).toLocaleString("zh-CN");
  const fmtMB = (b) => `${((b || 0) / 1048576).toFixed(2)} MB`;
  const fmtTime = (t) => `${(t || 0).toFixed(1)} s`;

  async function api(path, method = "GET", body = null) {
    const opts = { method, headers: {} };
    if (body !== null) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (e) { /* ignore */ }
      throw new Error(detail);
    }
    const type = res.headers.get("content-type") || "";
    return type.includes("application/json") ? res.json() : res;
  }

  // ------------------------------------------------------------- canvas
  function resize() {
    const rect = canvas.parentElement.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.floor(rect.width * dpr));
    canvas.height = Math.max(1, Math.floor(rect.height * dpr));
    canvas.style.width = `${rect.width}px`;
    canvas.style.height = `${rect.height}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    computeTransform();
    draw();
  }

  function computeTransform() {
    if (!net || !net.bounds) return;
    const b = net.bounds;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    const pad = 28;
    const scale = Math.min((w - pad * 2) / Math.max(1, b.xmax - b.xmin),
                           (h - pad * 2) / Math.max(1, b.ymax - b.ymin));
    transform = {
      scale, xmin: b.xmin, ymax: b.ymax,
      ox: (w - (b.xmax - b.xmin) * scale) / 2,
      oy: (h - (b.ymax - b.ymin) * scale) / 2,
    };
  }

  function toScreen(x, y) {
    return [transform.ox + (x - transform.xmin) * transform.scale,
            transform.oy + (transform.ymax - y) * transform.scale];
  }

  function draw() {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    ctx.clearRect(0, 0, w, h);
    drawBackground();
    if (!net) { drawHint("等待路网数据…"); return; }
    drawLanes();
    drawSensors();
    if (state) drawVehicles();
    if (selected) drawSelection();
  }

  function drawBackground() {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    const g = ctx.createLinearGradient(0, 0, 0, h);
    g.addColorStop(0, "#0a1017");
    g.addColorStop(1, "#0c141d");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, h);
    ctx.strokeStyle = "rgba(70,90,110,.08)";
    ctx.lineWidth = 1;
    for (let x = 0; x < w; x += 40) {
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
    }
    for (let y = 0; y < h; y += 40) {
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
    }
  }

  function drawLanes() {
    for (const lane of net.lanes || []) {
      if (!lane.shape || lane.shape.length < 2) continue;
      ctx.beginPath();
      lane.shape.forEach((p, i) => {
        const [sx, sy] = toScreen(p.x, p.y);
        if (i === 0) ctx.moveTo(sx, sy); else ctx.lineTo(sx, sy);
      });
      ctx.strokeStyle = "#22303f";
      ctx.lineWidth = Math.max(2, (lane.width || 3.2) * transform.scale);
      ctx.lineCap = "butt";
      ctx.stroke();
      ctx.strokeStyle = "rgba(120,160,200,.10)";
      ctx.lineWidth = 1;
      ctx.setLineDash([6, 10]);
      ctx.stroke();
      ctx.setLineDash([]);
    }
    for (const j of net.junctions || []) {
      const [sx, sy] = toScreen(j.x, j.y);
      ctx.fillStyle = "rgba(40,56,72,.6)";
      ctx.beginPath(); ctx.arc(sx, sy, 5, 0, Math.PI * 2); ctx.fill();
    }
    if (net.traffic_lights && net.traffic_lights.length) {
      for (const t of net.traffic_lights) {
        const [sx, sy] = toScreen(t.x, t.y);
        ctx.fillStyle = "#f0a93b";
        ctx.beginPath(); ctx.arc(sx, sy, 4, 0, Math.PI * 2); ctx.fill();
      }
    }
  }

  function drawSensors() {
    const sensors = (state && state.infrastructure) || [
      { id: "RSU-01", x: 60, y: 6, kind: "rsu", range: 220, fov: 120, yaw: 0 },
      { id: "RSU-02", x: 200, y: 6, kind: "rsu", range: 220, fov: 120, yaw: 0 },
      { id: "RSU-03", x: 340, y: 6, kind: "rsu", range: 220, fov: 120, yaw: 180 },
      { id: "GNB-01", x: 200, y: -40, kind: "gnb", range: 400, fov: 140, yaw: 30 },
    ];
    for (const s of sensors) {
      const [sx, sy] = toScreen(s.x, s.y);
      const color = s.kind === "rsu" ? "#3ba7ff" : "#35c48a";
      if (s.kind === "rsu") {
        ctx.beginPath();
        ctx.arc(sx, sy, s.range * transform.scale, 0, Math.PI * 2);
        ctx.strokeStyle = "rgba(59,167,255,.10)";
        ctx.lineWidth = 1;
        ctx.stroke();
      }
      ctx.beginPath();
      ctx.arc(sx, sy, s.kind === "rsu" ? 5 : 7, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.strokeStyle = "rgba(255,255,255,.5)";
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.fillStyle = "rgba(150,175,200,.75)";
      ctx.font = "10px sans-serif";
      ctx.fillText(s.id, sx + 8, sy - 6);
    }
  }

  function vehicleColor(v) {
    if (v.flags && v.flags.length) return "#f0a93b";
    if (v.cls === "truck") return "#6b7f95";
    return "#b8c8da";
  }

  function drawVehicles() {
    const list = state.vehicles || [];
    for (const v of list) {
      const [sx, sy] = toScreen(v.x, v.y);
      const l = Math.max(6, (v.length || 4.5) * transform.scale);
      const w = Math.max(3, (v.width || 1.8) * transform.scale);
      ctx.save();
      ctx.translate(sx, sy);
      ctx.rotate(-(v.heading || 0) * Math.PI / 180);
      ctx.fillStyle = vehicleColor(v);
      ctx.strokeStyle = v.id === selected ? "#ffffff" : "rgba(6,12,18,.8)";
      ctx.lineWidth = v.id === selected ? 2 : 1;
      ctx.beginPath();
      ctx.roundRect(-l / 2, -w / 2, l, w, 2);
      ctx.fill();
      ctx.stroke();
      if (v.flags && v.flags.length) {
        ctx.strokeStyle = "#f0a93b";
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        ctx.arc(0, 0, Math.max(l, w) * 0.85, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.restore();
    }
  }

  function drawSelection() {
    const v = (state.vehicles || []).find((x) => x.id === selected);
    if (!v) return;
    const [sx, sy] = toScreen(v.x, v.y);
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.arc(sx, sy, 16, 0, Math.PI * 2);
    ctx.stroke();
  }

  function drawHint(text) {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    ctx.fillStyle = "rgba(125,143,163,.8)";
    ctx.font = "13px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(text, w / 2, h / 2);
    ctx.textAlign = "left";
  }

  // -------------------------------------------------------------- render
  function setStateBadge(s) {
    const badge = $("stateBadge");
    const map = {
      idle: ["未启动", "idle"], running: ["运行中", "running"],
      paused: ["已暂停", "paused"], stopped: ["已停止", "idle"],
      starting: ["启动中", "starting"], error: ["错误", "error"],
    };
    const [text, cls] = map[s] || [s, "idle"];
    badge.textContent = text;
    badge.className = `badge ${cls}`;
  }

  function updateHeader(s) {
    setStateBadge(s.state);
    $("simTime").textContent = fmtTime(s.sim_time);
    $("speedLabel").textContent = `${(s.speed || 1).toFixed(1)}×`;
    $("vehCount").textContent = fmtInt((s.vehicles || []).length);
    $("runId").textContent = s.run_id ? `run: ${s.run_id}` : "未启动";
    const running = s.state === "running";
    const paused = s.state === "paused";
    $("btnStart").disabled = !(s.state === "idle" || s.state === "stopped" || s.state === "error");
    $("btnPause").disabled = !running;
    $("btnResume").disabled = !paused;
    $("btnStop").disabled = !(running || paused);
    $("btnReset").disabled = !(running || paused || s.state === "stopped");
    $("btnInject").disabled = !(running || paused);
    $("mapHint").style.display = (s.state === "running" || s.state === "paused") ? "none" : "block";
  }

  function updateStats(s) {
    const a = s.attacks || {};
    const primRec = Object.values(a.primitive_records || {}).reduce((x, y) => x + y, 0);
    $("statRecords").textContent = fmtInt(s.records_total);
    $("statBytes").textContent = fmtMB(s.bytes_total);
    $("statPrim").textContent = fmtInt(a.primitive_total);
    $("statPrimRec").textContent = `${fmtInt(primRec)} 条注入记录`;
    $("statCamp").textContent = fmtInt(a.campaign_total);
    $("statCampState").textContent = `进行 ${a.campaigns_active || 0} / 完成 ${a.campaigns_done || 0}`;
    $("statAlerts").textContent = fmtInt(s.alert_total);
    $("statEntities").textContent = `${s.anomaly_entities || 0} 个异常实体`;
    const lat = s.latency_ms || {};
    $("statLatency").textContent = `${(lat.p95 || 0).toFixed(1)} ms`;
    $("statLatencyN").textContent = `n=${lat.n || 0} · P50 ${(lat.p50 || 0).toFixed(1)}`;

    const box = $("campaignTypes");
    box.innerHTML = "";
    for (const key of ["A1", "A2", "A3", "A4"]) {
      const n = (a.campaigns || {})[key] || 0;
      const div = document.createElement("div");
      div.className = `type ${key}`;
      div.innerHTML = `<span class="name">${key} · ${CAMPAIGN_NAMES[key]}</span><span class="count">${n}</span>`;
      box.appendChild(div);
    }

    const tbody = $("streamTable").querySelector("tbody");
    tbody.innerHTML = "";
    const streams = Object.entries(s.streams || {}).sort((x, y) => y[1].records - x[1].records);
    for (const [name, v] of streams) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${name}</td><td class="num">${fmtInt(v.records)}</td><td class="num">${fmtMB(v.bytes)}</td>`;
      tbody.appendChild(tr);
    }
  }

  function updateEntities(s) {
    const vehicles = s.vehicles || [];
    const counts = { car: 0, truck: 0, victim: 0 };
    for (const v of vehicles) {
      if (v.flags && v.flags.length) counts.victim += 1;
      else if (v.cls === "truck") counts.truck += 1;
      else counts.car += 1;
    }
    const infra = s.infrastructure || [];
    const rsu = infra.filter((x) => x.kind === "rsu").length;
    const gnb = infra.filter((x) => x.kind === "gnb").length;
    const active = (s.attack_instances || []).filter((a) => a.started && !a.finished).length;
    $("entitySummary").innerHTML = [
      ["车辆总数", vehicles.length], ["普通车", counts.car],
      ["大型车", counts.truck], ["被攻击", counts.victim],
      ["RSU / gNB", `${rsu} / ${gnb}`], ["活跃攻击源", active],
    ].map(([k, v]) => `<div class="item"><span>${k}</span><b>${fmtInt(v)}</b></div>`).join("");

    const tbody = $("vehicleTable").querySelector("tbody");
    tbody.innerHTML = "";
    const rows = vehicles.slice(0).sort((a, b) => (b.flags || []).length - (a.flags || []).length || a.id.localeCompare(b.id)).slice(0, 80);
    for (const v of rows) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${v.id}</td><td>${v.cls}</td><td class="num">${v.speed.toFixed(1)} m/s</td>
        <td>${(v.flags || []).join(", ") || "—"}</td>`;
      tr.onclick = () => { selected = v.id; draw(); };
      tbody.appendChild(tr);
    }
  }

  function updateAttacks(s) {
    const list = s.attack_instances || [];
    const box = $("attackList");
    box.innerHTML = "";
    if (!list.length) {
      box.innerHTML = `<p class="muted small">本次运行尚未注入攻击</p>`;
      return;
    }
    const t = s.sim_time || 0;
    for (const a of list.slice().reverse()) {
      const dur = Math.max(0.1, (a.t1 || 0) - (a.t0 || 0));
      const pct = Math.max(0, Math.min(100, ((t - a.t0) / dur) * 100));
      const status = a.finished ? "已结束" : (a.started ? "进行中" : "等待中");
      const div = document.createElement("div");
      div.className = "attack";
      div.innerHTML = `
        <header><b>${a.type}</b><span class="tag">${a.kind === "campaign" ? "跨层" : "单体"} · ${status}</span></header>
        <div class="meta"><span>${a.label || ""}</span><span>${(a.t0 || 0).toFixed(1)}s → ${(a.t1 || 0).toFixed(1)}s</span></div>
        <div class="bar"><i style="width:${pct}%"></i></div>`;
      box.appendChild(div);
    }
  }

  function pushLogs(s) {
    const items = [];
    for (const n of s.notes || []) {
      items.push({ key: `note:${n.t}:${n.msg}`, t: n.t, lv: n.level, msg: n.msg });
    }
    const det = s.detector || {};
    for (const a of det.recent || []) {
      items.push({ key: `alert:${a.t}:${a.rule}:${a.entity}`, t: a.t, lv: "alert",
                   msg: `${a.rule} @ ${a.entity}` });
    }
    const list = $("logList");
    for (const it of items.reverse()) {
      if (logKeys.has(it.key)) continue;
      logKeys.add(it.key);
      const li = document.createElement("li");
      li.innerHTML = `<time>${it.t.toFixed(1)}s</time><span class="lv-${it.lv}">${it.msg}</span>`;
      list.prepend(li);
    }
    while (list.children.length > 300) list.removeChild(list.lastChild);
  }

  function updateAll(s) {
    state = s;
    if (!net && s.run_id && !netRequested) {
      netRequested = true;
      api("/api/net").then((data) => {
        if (data && data.lanes && data.lanes.length) { net = data; computeTransform(); draw(); }
      }).catch(() => { netRequested = false; });
    }
    updateHeader(s);
    updateStats(s);
    updateEntities(s);
    updateAttacks(s);
    pushLogs(s);
    if (s.error) pushLogs({ notes: [{ t: s.sim_time || 0, level: "alert", msg: `错误：${s.error}` }] });
    requestAnimationFrame(draw);
  }

  // ------------------------------------------------------------ controls
  async function post(path, body = {}) {
    try {
      const res = await api(path, "POST", body);
      if (res && res.state !== undefined) updateAll(res);
      return res;
    } catch (err) {
      alert(err.message);
      return null;
    }
  }

  $("btnStart").onclick = () => post("/api/session/start", {
    seed: 11,
    duration: Number($("durationInput").value || 60),
    speed: Number($("speedSelect").value),
    scene: "arterial",
  });
  $("btnPause").onclick = () => post("/api/session/pause");
  $("btnResume").onclick = () => post("/api/session/resume");
  $("btnStop").onclick = () => post("/api/session/stop");
  $("btnReset").onclick = () => post("/api/session/reset");
  $("speedSelect").onchange = (e) => post("/api/session/speed", { speed: Number(e.target.value) });

  $("attackAmplitude").oninput = (e) => {
    $("amplitudeOut").textContent = Number(e.target.value).toFixed(2);
  };

  $("attackForm").onsubmit = async (e) => {
    e.preventDefault();
    const body = {
      type: $("attackType").value,
      count: Number($("attackCount").value),
      amplitude: Number($("attackAmplitude").value),
      duration: Number($("attackDuration").value),
      start: Number($("attackStart").value) > 0
        ? (state ? state.sim_time + Number($("attackStart").value) : null)
        : null,
    };
    const res = await post("/api/attacks", body);
    if (res) {
      logKeys.add(`note:${state?.sim_time}:已注入 ${body.type} ×${body.count}`);
      if (state) pushLogs({ notes: [{ t: state.sim_time, level: "attack",
        msg: `已注入 ${body.type} ×${body.count}（强度 ${body.amplitude}）` }] });
    }
  };

  document.querySelectorAll(".tabs button").forEach((btn) => {
    btn.onclick = () => {
      document.querySelectorAll(".tabs button").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-pane").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      $(`tab-${btn.dataset.tab}`).classList.add("active");
      if (btn.dataset.tab === "runs") loadRuns();
    };
  });

  async function loadRuns() {
    try {
      const runs = await api("/api/runs");
      const list = $("runList");
      list.innerHTML = "";
      if (!runs.length) { list.innerHTML = `<li class="muted">暂无运行产物</li>`; return; }
      for (const r of runs) {
        const li = document.createElement("li");
        li.innerHTML = `<span>${r.run_id}<br><span class="muted">${r.events} 个真值事件 · ${r.size_mb} MB</span></span>
          <a href="/api/runs/${encodeURIComponent(r.run_id)}/download">下载</a>`;
        list.appendChild(li);
      }
    } catch (err) { /* ignore */ }
  }
  $("btnRefreshRuns").onclick = loadRuns;
  $("btnClearLog").onclick = () => { $("logList").innerHTML = ""; logKeys = new Set(); };

  // ------------------------------------------------------------ pointer
  function pickVehicle(ev) {
    if (!state) return null;
    const rect = canvas.getBoundingClientRect();
    const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
    let best = null, bestD = 14;
    for (const v of state.vehicles || []) {
      const [sx, sy] = toScreen(v.x, v.y);
      const d = Math.hypot(sx - mx, sy - my);
      if (d < bestD) { bestD = d; best = v; }
    }
    return best;
  }

  canvas.addEventListener("mousemove", (ev) => {
    const v = pickVehicle(ev);
    hover = v;
    const tip = $("tooltip");
    if (!v) { tip.style.display = "none"; canvas.style.cursor = "crosshair"; return; }
    const rect = canvas.getBoundingClientRect();
    tip.style.display = "block";
    tip.style.left = `${ev.clientX - rect.left + 14}px`;
    tip.style.top = `${ev.clientY - rect.top + 12}px`;
    tip.innerHTML = `<b>${v.id}</b><br>类型：${v.cls}<br>速度：${v.speed.toFixed(1)} m/s<br>
      ${(v.flags || []).length ? `被攻击：${v.flags.join(", ")}` : "状态：正常"}`;
    canvas.style.cursor = "pointer";
  });

  canvas.addEventListener("mouseleave", () => { $("tooltip").style.display = "none"; });
  canvas.addEventListener("click", (ev) => {
    const v = pickVehicle(ev);
    selected = v ? v.id : null;
    draw();
  });

  // ------------------------------------------------------------- init
  window.addEventListener("resize", resize);

  async function init() {
    resize();
    try {
      const hw = await api("/api/hardware");
      const sumo = hw.sumo && !hw.sumo.error ? hw.sumo.version.split(" ").slice(-1)[0] : "未安装";
      $("hwInfo").textContent = `CPU ${hw.cpu_count} 线程 · 内存 ${hw.mem_available_gib}/${hw.mem_total_gib} GiB · 磁盘 ${hw.disk_free_gib} GiB · SUMO ${sumo} · 生成器 isacsim-0.1.0`;
    } catch (e) { $("hwInfo").textContent = "硬件信息不可用"; }

    const source = new EventSource("/api/stream");
    source.onopen = () => { $("connInfo").textContent = "实时连接已建立"; };
    source.onerror = () => { $("connInfo").textContent = "实时连接断开，正在重连…"; };
    source.onmessage = (ev) => {
      try { updateAll(JSON.parse(ev.data)); } catch (e) { /* ignore */ }
    };
    const snap = await api("/api/state");
    if (snap) updateAll(snap);
  }

  init();
})();
