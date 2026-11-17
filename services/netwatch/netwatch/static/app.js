/* MyHomeLab — app web (sin dependencias externas) */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const view = $("#view");
const COLORS = ["--c1", "--c2", "--c3", "--c4", "--c5", "--c6", "--c7", "--c8"].map(
  (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim());
let timer = null;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 2500);
}
const ago = (ts) => {
  if (!ts) return "—";
  const s = Date.now() / 1000 - ts;
  if (s < 90) return "ahora";
  if (s < 3600) return `hace ${Math.round(s / 60)} min`;
  if (s < 86400) return `hace ${Math.round(s / 3600)} h`;
  return `hace ${Math.round(s / 86400)} d`;
};
const fmtTime = (ts) => new Date(ts * 1000).toLocaleString("es-MX", { dateStyle: "short", timeStyle: "short" });
const bytes = (n) => {
  if (n == null) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
};
const bps = (n) => {
  const u = ["bps", "Kbps", "Mbps", "Gbps"]; let i = 0;
  while (n >= 1000 && i < u.length - 1) { n /= 1000; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
};

/* ----------------------------------------------------------- gráficas SVG */
function lineChart(series, unit = "bps") {
  const W = 800, H = 240, P = { l: 64, r: 12, t: 10, b: 26 };
  const pts = series.flatMap((s) => s.points);
  if (!pts.length) return `<p class="muted">Sin datos todavía (Prometheus tarda unos minutos en juntar historial).</p>`;
  const x0 = Math.min(...pts.map((p) => p[0])), x1 = Math.max(...pts.map((p) => p[0]));
  const y1 = Math.max(...pts.map((p) => p[1]), unit === "s" ? 0.01 : 1) * 1.1;
  const X = (t) => P.l + ((t - x0) / Math.max(1, x1 - x0)) * (W - P.l - P.r);
  const Y = (v) => H - P.b - (v / y1) * (H - P.t - P.b);
  const fmt = unit === "s" ? (v) => `${(v * 1000).toFixed(0)} ms` : bps;
  let g = "";
  for (let i = 0; i <= 4; i++) {
    const v = (y1 / 4) * i;
    g += `<line x1="${P.l}" x2="${W - P.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="#2a2f3d"/>
          <text x="${P.l - 6}" y="${Y(v) + 4}" text-anchor="end" fill="#8a91a5" font-size="11">${fmt(v)}</text>`;
  }
  for (let i = 0; i <= 5; i++) {
    const t = x0 + ((x1 - x0) / 5) * i;
    const d = new Date(t * 1000);
    const lbl = x1 - x0 > 172800 ? d.toLocaleDateString("es-MX", { day: "2-digit", month: "short" })
      : d.toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit" });
    g += `<text x="${X(t)}" y="${H - 6}" text-anchor="middle" fill="#8a91a5" font-size="11">${lbl}</text>`;
  }
  const lines = series.map((s, i) => {
    const c = COLORS[i % COLORS.length];
    const d = s.points.map((p, j) => `${j ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("");
    const area = `${d}L${X(s.points.at(-1)[0])},${Y(0)}L${X(s.points[0][0])},${Y(0)}Z`;
    return `<path d="${area}" fill="${c}" opacity=".08"/><path d="${d}" stroke="${c}" stroke-width="2" fill="none"/>`;
  }).join("");
  const legend = series.map((s, i) => {
    const vals = s.points.map((p) => p[1]);
    const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
    return `<span><i style="background:${COLORS[i % COLORS.length]}"></i>${esc(s.name)} · prom ${fmt(avg)} · máx ${fmt(Math.max(...vals))}</span>`;
  }).join("");
  const id = "c" + Math.random().toString(36).slice(2, 8);
  CHARTS[id] = { series, x0, x1, P, W, H, fmt, X, Y };
  return `<div class="chartwrap" data-chart="${id}"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${g}${lines}
    <line class="xh" x1="0" x2="0" y1="${P.t}" y2="${H - P.b}" stroke="#8a91a5" stroke-dasharray="3 3" visibility="hidden"/></svg>
    <div class="tip" hidden></div></div><div class="legend">${legend}</div>`;
}

/* Cursor sobre una gráfica: línea vertical + valores de cada serie en ese momento */
const CHARTS = {};
document.addEventListener("mousemove", (e) => {
  const wrap = e.target.closest?.(".chartwrap");
  document.querySelectorAll(".chartwrap").forEach((w) => {
    if (w !== wrap) { w.querySelector(".tip").hidden = true; w.querySelector(".xh").setAttribute("visibility", "hidden"); }
  });
  if (!wrap) return;
  const c = CHARTS[wrap.dataset.chart]; if (!c) return;
  const svg = wrap.querySelector("svg"), r = svg.getBoundingClientRect();
  const vx = ((e.clientX - r.left) / r.width) * c.W;
  if (vx < c.P.l || vx > c.W - c.P.r) return;
  const t = c.x0 + ((vx - c.P.l) / (c.W - c.P.l - c.P.r)) * (c.x1 - c.x0);
  const rows = c.series.map((s, i) => {
    let best = s.points[0];
    for (const p of s.points) if (Math.abs(p[0] - t) < Math.abs(best[0] - t)) best = p;
    return `<div><i style="background:${COLORS[i % COLORS.length]}"></i>${esc(s.name)}: <b>${c.fmt(best[1])}</b></div>`;
  });
  const xh = wrap.querySelector(".xh"); xh.setAttribute("x1", vx); xh.setAttribute("x2", vx); xh.setAttribute("visibility", "visible");
  const tip = wrap.querySelector(".tip");
  tip.innerHTML = `<div class="muted small">${new Date(t * 1000).toLocaleString("es-MX", { dateStyle: "short", timeStyle: "short" })}</div>${rows.join("")}`;
  tip.hidden = false;
  const left = e.clientX - r.left + 14;
  tip.style.left = (left + 220 > r.width ? left - 240 : left) + "px";
  tip.style.top = (e.clientY - r.top + 10) + "px";
});

const ACTION_COLOR = { permitida: "#3ecf8e", bloqueada: "#f2555a", honeypot: "#c084fc" };
function hourlyBars(rows, hours) {
  const W = 800, H = 200, P = { l: 40, r: 10, t: 10, b: 24 };
  const now = Math.floor(Date.now() / 3600000) * 3600;
  const start = now - (hours - 1) * 3600;
  const buckets = {};
  rows.forEach((r) => { (buckets[r.hour] ??= {})[r.action] = r.n; });
  const n = hours, bw = (W - P.l - P.r) / n;
  const max = Math.max(1, ...Object.values(buckets).map((b) => Object.values(b).reduce((a, x) => a + x, 0)));
  let bars = "";
  for (let i = 0; i < n; i++) {
    const h = start + i * 3600, b = buckets[h] || {};
    let y = H - P.b;
    const total = Object.values(b).reduce((a, x) => a + x, 0);
    const label = `${new Date(h * 1000).toLocaleString("es-MX", { dateStyle: "short", timeStyle: "short" })}: ` +
      (Object.entries(b).map(([k, v]) => `${k} ${v}`).join(", ") || "sin conexiones");
    for (const act of ["permitida", "bloqueada", "honeypot"]) {
      if (!b[act]) continue;
      const hh = (b[act] / max) * (H - P.t - P.b);
      y -= hh;
      bars += `<rect x="${P.l + i * bw + 1}" y="${y}" width="${Math.max(1, bw - 2)}" height="${hh}" fill="${ACTION_COLOR[act]}"><title>${esc(label)}</title></rect>`;
    }
    if (!total) bars += `<rect x="${P.l + i * bw}" y="${P.t}" width="${bw}" height="${H - P.t - P.b}" fill="transparent"><title>${esc(label)}</title></rect>`;
    if (i % Math.ceil(n / 8) === 0) bars += `<text x="${P.l + i * bw}" y="${H - 6}" fill="#8a91a5" font-size="11">${
      hours > 48 ? new Date(h * 1000).toLocaleDateString("es-MX", { day: "2-digit", month: "short" })
                 : new Date(h * 1000).toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit" })}</text>`;
  }
  const axis = [0, 0.5, 1].map((f) => `<text x="${P.l - 6}" y="${H - P.b - f * (H - P.t - P.b) + 4}" text-anchor="end" fill="#8a91a5" font-size="11">${Math.round(max * f)}</text>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="height:200px">${axis}${bars}</svg>
    <div class="legend">${Object.entries(ACTION_COLOR).map(([k, c]) => `<span><i style="background:${c}"></i>${k}</span>`).join("")}</div>`;
}

/* Grafo origen → servicio: IPs a la izquierda, puertos de la Pi a la derecha */
const ORIGIN_COLOR = (o) => o.startsWith("Internet") ? "#f2555a" : o === "VPN" ? "#c084fc" : o.includes("vía") ? "#f5a524"
  : o.startsWith("lab") ? "#2dd4bf" : "#5aa9ff";
function flowGraph(ips, onlyAction) {
  const top = ips.slice(0, 30);
  const svc = {};
  top.forEach((r) => Object.entries(r.ports).forEach(([p, acts]) => {
    const n = Object.entries(acts).filter(([a]) => !onlyAction || a === onlyAction).reduce((s, [, v]) => s + v, 0);
    if (n) (svc[p] ??= { port: p, name: r.services[p], n: 0 }).n += n;
  }));
  const svcs = Object.values(svc).sort((a, b) => b.n - a.n);
  if (!top.length || !svcs.length) return '<p class="muted">Sin conexiones en este periodo.</p>';
  const rowH = 26, H = Math.max(top.length, svcs.length) * rowH + 20, W = 900, xl = 240, xr = 640;
  const yIp = (i) => 14 + i * rowH, ySv = (i) => 14 + i * ((H - 28) / Math.max(1, svcs.length - 1 || 1));
  const maxN = Math.max(...top.map((r) => r.total), 1);
  let links = "", nodes = "";
  top.forEach((r, i) => {
    Object.entries(r.ports).forEach(([p, acts]) => {
      Object.entries(acts).forEach(([a, n]) => {
        if (!n || (onlyAction && a !== onlyAction)) return;
        const j = svcs.findIndex((s) => s.port === p); if (j < 0) return;
        const y1 = yIp(i), y2 = svcs.length === 1 ? H / 2 : ySv(j), mx = (xl + xr) / 2;
        links += `<path class="lk" data-ip="${r.ip}" data-port="${p}" d="M${xl},${y1}C${mx},${y1} ${mx},${y2} ${xr},${y2}"
          stroke="${ACTION_COLOR[a]}" stroke-width="${1 + 7 * Math.sqrt(n / maxN)}" fill="none" opacity=".45"><title>${esc(r.ip)} → ${esc(r.services[p])} (${p}): ${n} ${a}</title></path>`;
      });
    });
    const label = r.name ? `${r.name} · ${r.ip}` : r.ip;
    nodes += `<g class="nd" data-ip="${r.ip}" style="cursor:pointer"><circle cx="${xl}" cy="${yIp(i)}" r="6" fill="${ORIGIN_COLOR(r.origin)}"/>
      <text x="${xl - 12}" y="${yIp(i) + 4}" text-anchor="end">${esc(label.slice(0, 34))}</text>
      <title>${esc(r.ip)} · ${esc(r.origin)} · ${r.total} conexiones</title></g>`;
  });
  svcs.forEach((s, j) => {
    const y = svcs.length === 1 ? H / 2 : ySv(j);
    nodes += `<g class="sv" data-port="${s.port}"><rect x="${xr}" y="${y - 10}" width="190" height="20" rx="6" fill="#1f2330" stroke="#2a2f3d"/>
      <text x="${xr + 10}" y="${y + 4}">${esc(s.name)} <tspan fill="#8a91a5">:${s.port} · ${s.n}</tspan></text></g>`;
  });
  return `<svg class="flow" viewBox="0 0 ${W} ${H}" style="width:100%;height:${H}px">${links}${nodes}</svg>`;
}

async function chartCard(el, title, q, range) {
  el.innerHTML = `<h3>${title}</h3><div class="chart muted">Cargando…</div>`;
  try {
    const j = await api(`/api/traffic?q=${q}&range=${range}`);
    $(".chart", el).innerHTML = j.error && !j.series.length
      ? `<p class="muted">No pude consultar Prometheus: ${esc(j.error)}</p>` : lineChart(j.series, j.unit);
  } catch (e) { $(".chart", el).textContent = e.message; }
}

/* ------------------------------------------------------------------ vistas */
const views = {
  async resumen() {
    const [s, ev] = await Promise.all([api("/api/summary"), api("/api/events?limit=8")]);
    $("#mode").textContent = s.bridge_mode ? `modo puente · ${s.interface}` : `modo pasivo · ${s.interface}`;
    $("#mode").className = "pill " + (s.bridge_mode ? "ok" : "");
    const inf = Object.entries(s.infra).map(([n, v]) =>
      `<tr><td><span class="dot ${v.up ? "on" : "off"}"></span></td><td>${esc(n)}</td>
       <td class="mono">${esc(v.ip)}</td><td>${v.latency != null ? (v.latency * 1000).toFixed(0) + " ms" : "sin respuesta"}</td></tr>`).join("");
    const tg = Object.entries(s.targets).map(([n, v]) =>
      `<tr><td><span class="dot ${v.up ? "on" : "off"}"></span></td><td>${esc(n)}</td>
       <td class="mono">${esc(v.host)}</td><td>${v.latency != null ? (v.latency * 1000).toFixed(0) + " ms" : "sin respuesta"}</td></tr>`).join("");
    const types = Object.entries(s.by_type).sort((a, b) => b[1] - a[1]);
    const maxT = Math.max(1, ...types.map((t) => t[1]));
    view.innerHTML = `
      ${s.scan_error ? `<div class="banner">⚠ ${esc(s.scan_error)}</div>` : ""}
      <div class="grid">
        <div class="card kpi"><div class="l">En línea</div><div class="v">${s.devices_online}</div><div class="muted small">de ${s.devices_total} conocidos</div></div>
        <div class="card kpi"><div class="l">Nuevos hoy</div><div class="v" style="color:${s.new_today ? "var(--warn)" : "inherit"}">${s.new_today}</div><div class="muted small">${s.unknown} sin revisar</div></div>
        <div class="card kpi"><div class="l">Internet</div><div class="v">${s.targets.internet ? (s.targets.internet.up ? "✅" : "❌") : "—"}</div><div class="muted small">${s.targets.internet?.latency != null ? (s.targets.internet.latency * 1000).toFixed(0) + " ms" : ""}</div></div>
        <div class="card kpi"><div class="l">Eventos 24 h</div><div class="v">${s.events_24h}</div><div class="muted small">${s.critical_24h} críticos</div></div>
        <div class="card kpi"><div class="l">Reglas ACL</div><div class="v">${s.acl_rules}</div><div class="muted small">${s.bridge_mode ? "aplicadas" : "solo aviso (sin modo puente)"}</div></div>
        <div class="card kpi"><div class="l">Último escaneo</div><div class="v" style="font-size:20px;margin-top:10px">${ago(s.last_scan)}</div></div>
      </div><br>
      <div class="grid2">
        <div class="card" id="c-home"></div>
        <div class="card"><h3>Infraestructura</h3><table>${inf}${tg}</table></div>
        <div class="card"><h3>Redes ${s.roaming ? `<span class="pill ok">roaming cada ${Math.round(s.hop_every / 60)} min</span>` : ""}</h3><table>
          <tr><th>Red</th><th>SSID</th><th>Segmento</th><th>Dispositivos</th><th>Escaneo</th></tr>
          ${s.networks.map((n) => `<tr><td>${n.name === s.current_network ? "📍 " : ""}${esc(n.name)}</td><td>${esc(n.ssid || "—")}</td>
            <td class="mono">${esc(n.cidr)}</td><td>${n.devices ?? "—"}</td>
            <td>${n.error ? `<span class="pill bad" title="${esc(n.error)}">error</span>` : ago(n.last_scan)}</td></tr>`).join("")}</table>
          ${s.roaming ? `<p class="muted small">📍 = red donde está la Pi ahora. Último cambio de red: ${ago(s.last_hop)}.</p>` : ""}</div>
        <div class="card"><h3>Tipos de dispositivo en línea</h3><div class="bars">${types.map(([t, n]) =>
          `<div class="row"><span>${s.icons[t] || "❔"} ${esc(t)}</span><div class="bar" style="width:${(n / maxT) * 100}%"></div><b>${n}</b></div>`).join("") || '<p class="muted">Aún no hay escaneos.</p>'}</div></div>
        <div class="card"><h3>Últimos eventos <a href="#eventos" class="small">ver todos</a></h3>${evList(ev.events)}</div>
      </div>`;
    chartCard($("#c-home"), "Tráfico de la casa (24 h)", "home", "24h");
    api("/api/dns").then((d) => {
      if (!d.enabled) return;
      const card = document.createElement("div"); card.className = "card";
      card.innerHTML = `<h3>🛡 DNS (AdGuard) <a href="${esc(d.url)}" target="_blank" class="small">abrir</a></h3>
        ${d.error ? `<p class="muted">${esc(d.error)}</p>` : `<p>${d.queries} consultas · <b style="color:var(--warn)">${d.blocked}</b> bloqueadas · <b style="color:var(--bad)">${d.malware}</b> malware/phishing</p>
        <table><tr><th>Equipo</th><th>Consultas</th></tr>${d.top_clients.slice(0, 8).map((c) => `<tr><td>${esc(c.name || c.ip)} <span class="muted small">${c.ip}</span></td><td>${c.n}</td></tr>`).join("")}</table>`}`;
      view.querySelector(".grid2")?.append(card);
    }).catch(() => {});
  },

  async dispositivos() {
    const j = await api("/api/devices");
    let sortKey = "online", asc = false, filter = "";
    const draw = () => {
      let rows = j.devices.filter((d) => !filter || JSON.stringify(d).toLowerCase().includes(filter));
      rows.sort((a, b) => {
        const va = a[sortKey] ?? "", vb = b[sortKey] ?? "";
        const ipn = (ip) => (ip || "").split(".").reduce((s, o) => s * 256 + +o, 0);
        const r = sortKey === "ip" ? ipn(va) - ipn(vb) : (va > vb ? 1 : va < vb ? -1 : 0);
        return asc ? r : -r;
      });
      $("#tb").innerHTML = rows.map((d) => `
        <tr data-mac="${d.mac}">
          <td><span class="dot ${d.online ? "on" : ""}"></span></td>
          <td><input class="nm" value="${esc(d.name || "")}" placeholder="${esc(d.display_name)}" style="width:170px">
              ${d.known ? "" : '<span class="new">NUEVO</span>'}</td>
          <td class="small">${d.hostname ? `<b>${esc(d.hostname)}</b><div class="muted">${esc(d.hostname_src || "")}</div>` : '<span class="muted">—</span>'}</td>
          <td><select class="tp">${j.types.map((t) => `<option ${t === d.display_type ? "selected" : ""}>${t}</option>`).join("")}</select></td>
          <td class="mono">${esc(d.ip)}<div class="muted small">${esc(d.network || "")}</div></td>
          <td class="mono">${esc(d.mac)}${d.random_mac ? ' <span class="pill" title="MAC privada/aleatoria">privada</span>' : ""}<div class="muted small">${esc(d.vendor || "")}</div></td>
          <td><select class="ap"><option value="">—</option>${j.aps.map((a) => `<option ${a === d.ap ? "selected" : ""}>${esc(a)}</option>`).join("")}</select></td>
          <td class="small mono">${d.ports.map(esc).join("<br>") || '<span class="muted">—</span>'}</td>
          <td class="small">${d.bytes_down != null ? "↓ " + bytes(d.bytes_down) + "<br>↑ " + bytes(d.bytes_up) : '<span class="muted">—</span>'}</td>
          <td class="small">${ago(d.last_seen)}</td>
          <td style="white-space:nowrap">
            ${d.known ? "" : '<button class="ok" title="Ya lo revisé">✔</button>'}
            <button class="scan" title="Escanear puertos">🔍</button>
            <button class="blk danger" title="Bloquear (ACL)">⛔</button>
            <button class="del danger" title="Olvidar">🗑</button></td>
        </tr>`).join("");
    };
    view.innerHTML = `
      <div class="toolbar"><h2 class="grow">Dispositivos <span class="muted small">${j.devices.filter((d) => d.online).length} en línea · ${j.devices.length} conocidos</span></h2>
        <input id="flt" placeholder="Filtrar nombre, hostname, IP, MAC…" style="width:250px">
        <button id="seek" title="Busca la IP, MAC o nombre en la red aunque no esté en la lista">🔎 Buscar en la red</button>
        <button id="allknown">✔ Marcar todos como revisados</button></div>
      <div class="card" style="overflow:auto"><table><thead><tr>
        <th data-k="online"></th><th data-k="display_name">Nombre</th><th data-k="hostname">Hostname</th><th data-k="display_type">Tipo</th><th data-k="ip">IP / red</th>
        <th data-k="mac">MAC / fabricante</th><th data-k="ap">AP</th><th>Puertos</th><th data-k="bytes_down">Tráfico</th><th data-k="last_seen">Visto</th><th></th>
      </tr></thead><tbody id="tb"></tbody></table></div>
      <p class="muted small">El AP no se puede saber por ARP: asígnalo aquí y aparecerá en el mapa y en «Por AP». El tráfico por dispositivo solo existe en modo puente.</p>`;
    draw();
    $("#flt").oninput = (e) => { filter = e.target.value.toLowerCase(); draw(); };
    view.querySelectorAll("th[data-k]").forEach((th) => th.onclick = () => {
      asc = sortKey === th.dataset.k ? !asc : true; sortKey = th.dataset.k; draw();
    });
    $("#seek").onclick = async () => {
      const q = $("#flt").value.trim();
      if (!q) return toast("Escribe una IP, MAC o nombre en el filtro");
      const box = document.createElement("div");
      box.className = "card"; box.style.marginBottom = "12px";
      box.innerHTML = `<h3>🔎 Buscando «${esc(q)}» en la red… (ping, ARP, nombre y puertos; hasta 1 min)</h3>`;
      view.querySelector(".card").before(box);
      try {
        const r = await api("/api/lookup", { method: "POST", body: JSON.stringify({ q }) });
        const p = r.probe;
        box.innerHTML = `<h3>🔎 Resultado para «${esc(q)}»</h3>
          ${r.matches.length ? `<p>En la lista: ${r.matches.map((d) => `<b>${esc(d.display_name)}</b> <span class="mono small">${d.ip} · ${d.mac}</span>`).join(", ")}</p>` : ""}
          ${p ? `<table><tr><td>IP</td><td class="mono">${esc(p.ip)} ${p.network ? `(${esc(p.network)})` : "(fuera de tus redes)"}</td></tr>
            <tr><td>Responde</td><td>${p.reachable ? "✅" : "❌ no responde a ping ni ARP"} ${p.ping_ms != null ? p.ping_ms + " ms" : ""}</td></tr>
            <tr><td>MAC</td><td class="mono">${esc(p.mac || "—")} ${esc(p.vendor || "")}</td></tr>
            <tr><td>Nombre</td><td>${esc(p.hostname || "—")} <span class="muted small">${esc(p.hostname_src || "")}</span></td></tr>
            <tr><td>Tipo</td><td>${esc(p.type)}</td></tr><tr><td>Puertos</td><td class="mono small">${(p.ports || []).map(esc).join(", ") || "—"}</td></tr></table>
            ${p.note ? `<p class="muted">${esc(p.note)}</p>` : ""}${p.device ? '<p>✅ Agregado a la lista. Recarga para verlo.</p>' : ""}`
          : (!r.matches.length ? `<p class="muted">No encontré nada con ese nombre. Prueba con la IP (la ves en el equipo o en la lista del módem).</p>` : "")}`;
      } catch (e) { box.innerHTML = `<p class="banner">${esc(e.message)}</p>`; }
    };
    $("#allknown").onclick = async () => { await api("/api/devices/known", { method: "POST" }); route(); };
    const patch = (mac, body) => api(`/api/devices/${mac}`, { method: "PATCH", body: JSON.stringify(body) })
      .then((d) => { Object.assign(j.devices.find((x) => x.mac === mac), d); toast("Guardado"); });
    $("#tb").addEventListener("change", (e) => {
      const mac = e.target.closest("tr").dataset.mac;
      if (e.target.classList.contains("nm")) patch(mac, { name: e.target.value });
      if (e.target.classList.contains("tp")) patch(mac, { type_manual: e.target.value });
      if (e.target.classList.contains("ap")) patch(mac, { ap: e.target.value });
    });
    $("#tb").addEventListener("click", async (e) => {
      const b = e.target.closest("button"); if (!b) return;
      const mac = b.closest("tr").dataset.mac;
      if (b.classList.contains("ok")) { await patch(mac, { known: true }); draw(); }
      if (b.classList.contains("scan")) toast((await api(`/api/devices/${mac}/rescan`, { method: "POST" })).msg);
      if (b.classList.contains("blk") && confirm(`¿Bloquear ${mac}? (sin modo puente solo te avisará cuando se conecte)`)) {
        await api("/api/acl", { method: "POST", body: JSON.stringify({ mac, note: "desde Dispositivos" }) });
        toast("Regla agregada en ACL");
      }
      if (b.classList.contains("del") && confirm("¿Olvidar este dispositivo? Si vuelve a conectarse aparecerá como nuevo.")) {
        await api(`/api/devices/${mac}`, { method: "DELETE" });
        j.devices = j.devices.filter((d) => d.mac !== mac); draw();
      }
    });
  },

  async trafico() {
    view.innerHTML = `<div class="toolbar"><h2 class="grow">Tráfico</h2>
      <select id="rng"><option value="1h">1 hora</option><option value="6h">6 horas</option>
      <option value="24h" selected>24 horas</option><option value="7d">7 días</option></select>
      <a href="/grafana/d/homelab-red" target="_blank"><button>Abrir en Grafana ↗</button></a></div>
      <div class="grid2"><div class="card" id="t1"></div><div class="card" id="t2"></div>
      <div class="card" id="t3"></div><div class="card" id="t4"></div></div><br>
      <div class="grid2"><div class="card" id="tot"></div></div>`;
    const load = async () => {
      const r = $("#rng").value;
      chartCard($("#t1"), "🏠 Casa (interfaz de la Raspberry)", "home", r);
      chartCard($("#t2"), "☁ VPS", "vps", r);
      chartCard($("#t3"), "📈 Top dispositivos (modo puente)", "devices", r);
      chartCard($("#t4"), "⏱ Latencia", "latency", r);
    };
    $("#rng").onchange = load; load();
    const t = await api("/api/totals");
    const devs = {};
    (t.devices || []).forEach((d) => { devs[d.name] ??= { up: 0, down: 0 }; devs[d.name][d.direction] = d.bytes; });
    const rows = Object.entries(devs).sort((a, b) => (b[1].down + b[1].up) - (a[1].down + a[1].up)).slice(0, 15);
    $("#tot").innerHTML = `<h3>Consumo de hoy por dispositivo</h3>${t.error ? `<p class="muted">${esc(t.error)}</p>` : ""}
      ${rows.length ? `<table><tr><th>Dispositivo</th><th>Bajada</th><th>Subida</th></tr>${rows.map(([n, v]) =>
        `<tr><td>${esc(n)}</td><td>${bytes(v.down)}</td><td>${bytes(v.up)}</td></tr>`).join("")}</table>`
        : '<p class="muted">Necesita modo puente (ver docs/modo-puente.md).</p>'}
      ${(t.vps || []).length ? `<h3 style="margin-top:14px">VPS hoy</h3>${t.vps.map((v) => `${v.direction}: <b>${bytes(v.bytes)}</b>`).join(" · ")}` : ""}`;
  },

  async conexiones() {
    view.innerHTML = `<div class="toolbar"><h2 class="grow">Conexiones entrantes a la Pi</h2>
      <input id="cq" placeholder="Filtrar IP, nombre, origen…" style="width:220px">
      <select id="ca"><option value="">Todas</option><option value="permitida">Permitidas</option>
        <option value="bloqueada">Bloqueadas (UFW)</option><option value="honeypot">Honeypot</option></select>
      <select id="ch"><option value="1">1 hora</option><option value="6">6 horas</option><option value="24" selected>24 horas</option>
        <option value="168">7 días</option></select></div>
      <div id="cbody"><p class="muted">Cargando…</p></div>`;
    const load = async () => {
      const hours = +$("#ch").value || 24, act = $("#ca").value, q = $("#cq").value.toLowerCase();
      const j = await api(`/api/inbound?hours=${hours}`);
      let ips = j.ips.filter((r) => (!act || r.actions[act]) && (!q || `${r.ip} ${r.name} ${r.origin}`.toLowerCase().includes(q)));
      const sum = (a) => j.ips.reduce((s, r) => s + (r.actions[a] || 0), 0);
      const warn = [!j.sources.conntrack && "conntrack", !j.sources.ufw && "log de UFW (ver docs/conexiones.md)"].filter(Boolean);
      $("#cbody").innerHTML = `
        ${warn.length ? `<div class="banner">Sin datos de: ${warn.join(", ")}</div>` : ""}
        <div class="grid">
          <div class="card kpi"><div class="l">IPs distintas</div><div class="v">${j.ips.length}</div></div>
          <div class="card kpi"><div class="l">Permitidas</div><div class="v" style="color:var(--ok)">${sum("permitida")}</div></div>
          <div class="card kpi"><div class="l">Bloqueadas</div><div class="v" style="color:var(--bad)">${sum("bloqueada")}</div></div>
          <div class="card kpi"><div class="l">Honeypot</div><div class="v" style="color:var(--c4)">${sum("honeypot")}</div></div>
          <div class="card kpi"><div class="l">Desde Internet</div><div class="v">${j.ips.filter((r) => r.origin === "Internet").length}</div><div class="muted small">IPs públicas</div></div>
        </div><br>
        <div class="card"><h3>Por hora</h3>${hourlyBars(j.hourly.filter((r) => !act || r.action === act), Math.min(hours, 168))}</div><br>
        <div class="card"><h3>Quién se conecta a qué <span class="muted small">(pasa el ratón; clic en una IP para el detalle)</span></h3>
          <div class="legend" style="margin:0 0 8px">${[["casa", "#5aa9ff"], ["laboratorio", "#2dd4bf"], ["otra red (vía Tenda)", "#f5a524"], ["VPN", "#c084fc"], ["Internet", "#f2555a"]]
            .map(([k, c]) => `<span><i style="background:${c};border-radius:50%"></i>${k}</span>`).join("")}</div>
          <div style="overflow-x:auto">${flowGraph(ips, act)}</div></div><br>
        <div class="grid2"><div class="card" style="overflow:auto"><h3>Detalle por IP</h3><table><tr><th>IP</th><th>Origen</th><th>Servicios</th><th>✔</th><th>⛔</th><th>🍯</th><th>Última</th></tr>
          ${ips.map((r) => `<tr class="iprow" data-ip="${r.ip}" style="cursor:pointer"><td class="mono">${esc(r.ip)}<div class="muted small">${esc(r.name)}</div></td>
            <td><span class="dot" style="background:${ORIGIN_COLOR(r.origin)}"></span> ${esc(r.origin)}</td>
            <td class="small">${Object.keys(r.ports).map((p) => esc(r.services[p])).join(", ")}</td>
            <td>${r.actions.permitida || ""}</td><td>${r.actions.bloqueada || ""}</td><td>${r.actions.honeypot || ""}</td>
            <td class="small">${ago(r.last)}</td></tr>`).join("") || '<tr><td colspan="7" class="muted">Nada todavía.</td></tr>'}</table></div>
          <div class="card" id="ipd"><h3>Selecciona una IP</h3><p class="muted">Haz clic en una IP del grafo o de la tabla.</p></div></div>`;
      const showIp = async (ip) => {
        const d = await api(`/api/inbound/${ip}`);
        const r = j.ips.find((x) => x.ip === ip) || { ports: {}, services: {}, actions: {} };
        $("#ipd").innerHTML = `<h3>${esc(ip)} · ${esc(d.origin)}</h3>
          ${r.name ? `<p>Equipo: <b>${esc(r.name)}</b> <span class="mono small">${esc(r.mac)}</span></p>` : ""}
          <table><tr><th>Servicio</th><th>Permitidas</th><th>Bloqueadas</th><th>Honeypot</th></tr>${Object.entries(r.ports).map(([p, a]) =>
            `<tr><td>${esc(r.services[p])} <span class="muted">:${p}</span></td><td>${a.permitida || ""}</td><td>${a.bloqueada || ""}</td><td>${a.honeypot || ""}</td></tr>`).join("")}</table>
          <h3 style="margin-top:12px">Por hora</h3>${hourlyBars(d.hourly, Math.min(hours, 168))}
          <h3 style="margin-top:12px">Eventos</h3>${evList(d.events.slice(0, 8))}
          <p class="small muted">Bloquearla en el firewall de la Pi:</p><code class="mono small" style="user-select:all">${esc(d.ufw)}</code>
          <button id="cpy" style="margin-left:8px">Copiar</button>`;
        $("#cpy").onclick = () => navigator.clipboard?.writeText(d.ufw).then(() => toast("Copiado"));
      };
      $("#cbody").querySelectorAll(".nd, .iprow").forEach((el) => el.onclick = () => showIp(el.dataset.ip));
      const svg = $("#cbody").querySelector("svg.flow");
      if (svg) svg.addEventListener("mouseover", (e) => {
        const g = e.target.closest(".nd, .sv"); if (!g) return;
        const key = g.classList.contains("nd") ? ["ip", g.dataset.ip] : ["port", g.dataset.port];
        svg.querySelectorAll(".lk").forEach((l) => l.setAttribute("opacity", l.dataset[key[0]] === key[1] ? ".95" : ".07"));
      });
      if (svg) svg.addEventListener("mouseleave", () => svg.querySelectorAll(".lk").forEach((l) => l.setAttribute("opacity", ".45")));
    };
    ["#ch", "#ca"].forEach((s) => $(s).onchange = load);
    let deb; $("#cq").oninput = () => { clearTimeout(deb); deb = setTimeout(load, 300); };
    await load();
  },

  async ap() {
    const j = await api("/api/by-ap");
    const types = [...new Set(Object.values(j.aps).flatMap((o) => Object.keys(o)))];
    view.innerHTML = `<h2>Dispositivos por Access Point</h2><div class="grid2">${Object.entries(j.aps).map(([ap, t]) => {
      const tot = Object.values(t).reduce((a, b) => a + b, 0);
      const max = Math.max(...Object.values(t));
      return `<div class="card"><h3>📡 ${esc(ap)} · ${tot} en línea</h3><div class="bars">${Object.entries(t).sort((a, b) => b[1] - a[1]).map(([k, n]) =>
        `<div class="row"><span>${j.icons[k] || "❔"} ${esc(k)}</span><div class="bar" style="width:${(n / max) * 100}%;background:${COLORS[types.indexOf(k) % COLORS.length]}"></div><b>${n}</b></div>`).join("")}</div></div>`;
    }).join("") || '<p class="muted">No hay dispositivos en línea.</p>'}</div>
    <p class="muted small">Asigna cada dispositivo a su AP en «Dispositivos».</p>`;
  },

  async mapa() {
    const t = await api("/api/topology");
    view.innerHTML = `<div class="toolbar"><h2 class="grow">Mapa de la red</h2>
      <label class="small"><input type="checkbox" id="onl" checked> solo en línea</label></div>
      <div class="card tree" id="tree"></div>
      <p class="muted small">Clic en el módem o un AP para abrir su administración.</p>`;
    const draw = () => $("#tree").innerHTML = treeSVG(t, $("#onl").checked);
    $("#onl").onchange = draw; draw();
  },

  async camaras() {
    const j = await api("/api/cameras");
    if (!j.cameras.length) {
      view.innerHTML = `<h2>Cámaras</h2><div class="card"><p>No pude leer las cámaras de Frigate.</p>
        <p class="muted">${esc(j.error || "")}</p><p class="muted small">Revisa <span class="mono">cameras.frigate_url</span> en config/homelab.yml y docs/camaras.md.</p></div>`;
      return;
    }
    view.innerHTML = `<div class="toolbar"><h2 class="grow">Cámaras</h2>
      <a href="${esc(j.frigate_url)}" target="_blank"><button>Abrir Frigate ↗</button></a></div>
      <div class="cams">${j.cameras.map((c) => `<div class="card cam"><h3>📷 ${esc(c)}</h3>
        <img data-cam="${esc(c)}" alt="${esc(c)}">
        <div class="toolbar" style="margin:8px 0 0">
          ${j.go2rtc_url ? `<a href="${esc(j.go2rtc_url)}/stream.html?src=${encodeURIComponent(c)}" target="_blank"><button>▶ En vivo</button></a>` : ""}
          <a href="${esc(j.frigate_url)}/review?cameras=${encodeURIComponent(c)}" target="_blank"><button>Grabaciones</button></a>
        </div></div>`).join("")}</div>`;
    const refresh = () => view.querySelectorAll("img[data-cam]").forEach((img) =>
      img.src = `/api/cameras/${img.dataset.cam}/snapshot?t=${Date.now()}`);
    refresh(); timer = setInterval(refresh, 5000);
  },

  async accesos() {
    const j = await api("/api/links");
    const groups = {};
    j.links.forEach((l) => (groups[l.group] ??= []).push(l));
    view.innerHTML = Object.entries(groups).map(([g, ls]) => `<h2>${esc(g)}</h2><div class="links">${ls.map((l) =>
      `<a class="link" href="${esc(l.url)}" target="_blank" rel="noopener"><span class="i">${l.icon || "🔗"}</span>
       <div><b>${esc(l.name)}</b> ${l.up === true ? '<span class="dot on"></span>' : l.up === false ? '<span class="dot off"></span>' : ""}
       <div class="muted small">${esc(l.detail || l.url)}</div></div></a>`).join("")}</div><br>`).join("");
  },

  async acl() {
    const [j, d] = await Promise.all([api("/api/acl"), api("/api/devices")]);
    view.innerHTML = `<h2>Control de acceso (ACL)</h2>
      ${j.enforced ? "" : `<div class="banner">Sin modo puente las reglas no cortan el tráfico: solo te avisan cuando el dispositivo se conecta.
        Para bloquear de verdad pon la Pi entre el módem y tus AP (docs/modo-puente.md).</div>`}
      <div class="card"><h3>Nueva regla</h3><div class="toolbar">
        <select id="am" style="max-width:340px"><option value="">Elige un dispositivo…</option>${d.devices.map((x) =>
          `<option value="${x.mac}">${esc(x.display_name)} · ${x.mac}</option>`).join("")}</select>
        <span class="muted">o MAC</span><input id="amac" placeholder="aa:bb:cc:dd:ee:ff" class="mono" style="width:170px">
        <span class="muted">horario (opcional)</span><input id="as" type="time"><span>a</span><input id="ae" type="time">
        <input id="an" placeholder="nota (ej. sin internet de noche)" style="width:220px">
        <button class="primary" id="aadd">⛔ Bloquear</button></div></div><br>
      <div class="card"><table><tr><th>Dispositivo</th><th>MAC</th><th>Horario</th><th>Ahora</th><th>Nota</th><th></th></tr>
      ${j.rules.map((r) => `<tr><td>${esc(r.name || "—")}</td><td class="mono">${r.mac}</td>
        <td>${r.start ? `${r.start} – ${r.end}` : "siempre"}</td>
        <td>${r.active_now ? '<span class="pill bad">bloqueado</span>' : '<span class="pill">fuera de horario</span>'}</td>
        <td class="muted">${esc(r.note || "")}</td><td><button class="danger" data-id="${r.id}">Quitar</button></td></tr>`).join("")
        || '<tr><td colspan="6" class="muted">Sin reglas.</td></tr>'}</table></div>`;
    $("#aadd").onclick = async () => {
      const mac = $("#amac").value.trim() || $("#am").value;
      try {
        await api("/api/acl", { method: "POST", body: JSON.stringify({ mac, start: $("#as").value, end: $("#ae").value, note: $("#an").value }) });
        route();
      } catch (e) { toast(e.message); }
    };
    view.querySelectorAll("button[data-id]").forEach((b) => b.onclick = async () => {
      await api(`/api/acl/${b.dataset.id}`, { method: "DELETE" }); route();
    });
  },

  async eventos() {
    const j = await api("/api/events?limit=300");
    view.innerHTML = `<h2>Registro de eventos anómalos</h2><div class="card">${evList(j.events)}</div>`;
  },
};

const EV_ICON = { new_device: "🆕", arp_spoof: "🕵", ports_changed: "🚪", ids: "🚨", blocked_seen: "⛔",
  quarantine: "🧪", acl: "🔒", honeypot: "🍯", dns_danger: "☣", roam_failed: "📶" };
function evList(evs) {
  if (!evs.length) return '<p class="muted">Sin eventos. Todo tranquilo.</p>';
  return evs.map((e) => `<div class="ev"><span>${EV_ICON[e.kind] || "•"}</span>
    <span class="t">${fmtTime(e.ts)}</span><div><b class="lvl-${e.level}">${esc(e.title)}</b>
    <span class="muted small">${esc(e.detail)}</span></div></div>`).join("");
}

/* -------------------------------------------- árbol (Internet → módem → AP → dispositivos) */
function treeSVG(root, onlyOnline) {
  const prune = (n) => ({ ...n, children: (n.children || []).filter((c) => !onlyOnline || c.online || c.children)
    .map(prune).filter((c) => !onlyOnline || c.online || (c.children && c.children.length) || c.icon === "📡") });
  const t = prune(root);
  const colW = 230, rowH = 34, boxW = 200, boxH = 26;
  let leaf = 0;
  const layout = (n, depth) => {
    n.x = 20 + depth * colW;
    if (!n.children || !n.children.length) { n.y = 20 + leaf++ * rowH; return; }
    n.children.forEach((c) => layout(c, depth + 1));
    n.y = (n.children[0].y + n.children.at(-1).y) / 2;
  };
  layout(t, 0);
  const H = Math.max(80, leaf * rowH + 30);
  let links = "", nodes = "";
  const walk = (n) => {
    (n.children || []).forEach((c) => {
      const x1 = n.x + boxW, y1 = n.y + boxH / 2, x2 = c.x, y2 = c.y + boxH / 2, mx = (x1 + x2) / 2;
      links += `<path d="M${x1},${y1}C${mx},${y1} ${mx},${y2} ${x2},${y2}"/>`;
      walk(c);
    });
    const label = `${n.icon || ""} ${n.name}`.slice(0, 28);
    const sub = n.ip || n.type || "";
    const g = `<g class="node ${n.online === false ? "off" : ""}" transform="translate(${n.x},${n.y})">
      <rect width="${boxW}" height="${boxH}"></rect>
      <text x="10" y="17">${esc(label)}</text>
      ${sub ? `<text class="sub" x="${boxW - 8}" y="17" text-anchor="end">${esc(sub)}</text>` : ""}
      ${n.children && n.children.length ? `<text class="sub" x="${boxW + 4}" y="-3">${n.children.length}</text>` : ""}</g>`;
    nodes += n.admin_url ? `<a href="${esc(n.admin_url)}" target="_blank">${g}</a>` : g;
  };
  walk(t);
  const W = 20 + 4 * colW + 20;
  return `<svg viewBox="0 0 ${W} ${H}" style="min-width:${W}px;height:${H}px">${links}${nodes}</svg>`;
}

/* ------------------------------------------------------------------ router */
async function route() {
  clearInterval(timer);
  const name = (location.hash || "#resumen").slice(1);
  document.querySelectorAll("#tabs a").forEach((a) => a.classList.toggle("on", a.getAttribute("href") === "#" + name));
  try {
    await (views[name] || views.resumen)();
  } catch (e) {
    view.innerHTML = `<div class="banner">Error: ${esc(e.message)}</div>`;
  }
  if (["resumen", "eventos"].includes(name)) timer = setInterval(() => (views[name])(), 60000);
}
window.addEventListener("hashchange", route);
route();
api("/api/summary").then((s) => {
  $("#mode").textContent = s.bridge_mode ? `modo puente · ${s.interface}` : `modo pasivo · ${s.interface}`;
  $("#mode").className = "pill " + (s.bridge_mode ? "ok" : "");
}).catch(() => {});
