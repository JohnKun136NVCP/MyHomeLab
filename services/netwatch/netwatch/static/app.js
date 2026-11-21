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
  if (r.status === 401 && path.startsWith("/api/") && !opts.noRedirect) {     // sesión caducada
    location.href = "/login?next=" + encodeURIComponent(location.pathname + location.hash);
    throw new Error("Sesión caducada");
  }
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

const ACTION_COLOR = { permitida: "#3ecf8e", bloqueada: "#f2555a" };
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
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:200px;display:block">${axis}${bars}</svg>
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
  async inicio() {
    if (!LOGS) return views.inicioFamilia();
    view.innerHTML = `<div class="toolbar"><h2 class="grow">Hola${ME ? ", " + esc(ME.user) : ""} 👋</h2>
      ${ADMIN ? '<a href="#sistema"><button>🛠 Sistema</button></a>' : ""}</div>
      <div class="grid" id="kpis"></div>
      <div class="grid2" id="status" style="margin-top:var(--gap)"></div>
      <div id="tools"><p class="muted">Revisando servicios…</p></div>`;
    api("/api/summary").then((s) => {
      const inet = s.targets.internet;
      $("#kpis").innerHTML = `
        <a class="card kpi" href="#dispositivos"><span class="l">Equipos en línea</span><span class="v">${s.devices_online}</span><span class="muted small">${s.unknown} sin revisar</span></a>
        <a class="card kpi" href="#eventos"><span class="l">Eventos 24 h</span><span class="v">${s.events_24h}</span><span class="muted small" style="color:${s.critical_24h ? "var(--bad)" : ""}">${s.critical_24h} críticos</span></a>
        <a class="card kpi" href="#resumen"><span class="l">Internet</span><span class="v">${inet ? (inet.up ? "✅" : "❌") : "—"}</span><span class="muted small">${inet?.latency != null ? (inet.latency * 1000).toFixed(0) + " ms" : "&nbsp;"}</span></a>
        <a class="card kpi" href="#servicios" id="k-svc"><span class="l">Servicios</span><span class="v">…</span><span class="muted small">Uptime Kuma</span></a>`;
      api("/api/services").then((v) => {
        $("#k-svc").innerHTML = `<span class="l">Servicios</span><span class="v" style="color:${v.down ? "var(--bad)" : ""}">${v.ok ? `${v.up}/${v.monitors.length}` : "—"}</span>
          <span class="muted small">${v.ok ? (v.down ? `${v.down} caído(s)` : "todo arriba") : "sin página de estado"}</span>`;
      }).catch(() => {});
    }).catch(() => {});
    if (ADMIN) api("/api/system").then((sy) => {
      const r = sy.revisar || {}, bk = sy.backup || {};
      $("#status").innerHTML = `<div class="card"><h3>🩺 Estado del homelab ${r.ts ? `<span class="muted small">revisado ${ago(r.ts)}</span>` : ""}</h3>
          ${r.ts ? `<p><span class="pill ok">✔ ${r.ok}</span> <span class="pill warn">⚠ ${r.warn}</span> <span class="pill bad">✘ ${r.bad}</span></p>
            ${(r.items || []).filter((i) => i.st !== "ok").slice(0, 4).map((i) => `<div class="small" style="margin:4px 0">${i.st === "bad" ? "✘" : "⚠"} ${esc(i.what)}</div>`).join("")}`
            : '<p class="muted">Todavía no se revisa.</p>'}
          <a href="#sistema" class="small">Ver todo →</a></div>
        <div class="card"><h3>🗄 Respaldo del homelab</h3>
          ${bk.ts ? `<p>${bk.ok ? "✅" : "⚠"} ${ago(bk.ts)} ${bk.size ? "· " + bytes(bk.size) : ""} ${(bk.sent || []).map((x) => `<span class="pill">${esc(x)}</span>`).join(" ")}</p>`
            : '<p class="muted">Nunca se ha respaldado.</p>'}
          <a href="#sistema" class="small">Respaldar ahora →</a></div>`;
    }).catch(() => {});
    const j = await api("/api/tools");
    const sections = [["Homelab", [
      { name: "Resumen", icon: "🏠", url: "#resumen", desc: "Estado de la red", status: true },
      { name: "Dispositivos", icon: "💻", url: "#dispositivos", desc: "Quién está conectado", status: true },
      { name: "Mapa", icon: "🗺", url: "#mapa", desc: "Módem → AP → equipos", status: true },
      ...j.tools.filter((t) => t.group === "Homelab")]]];
    for (const g of ["Servicios", "En casa", "Red", "Tus enlaces"]) {
      const ts = j.tools.filter((t) => t.group === g);
      if (ts.length) sections.push([g, ts]);
    }
    const tile = (t) => t.home && !t.enabled
      ? `<div class="link off"><span class="i">${t.icon || "🔗"}</span><div style="flex:1"><b>${esc(t.name)}</b> <span class="muted small">apagado</span>
          <div class="muted small">${esc(t.desc || "")}</div></div>
          ${ADMIN ? `<button class="small" data-home-on="${esc(t.home)}">Encender</button>` : ""}</div>`
      : `<a class="link" href="${esc(t.url)}" ${t.url.startsWith("#") ? "" : 'target="_blank" rel="noopener"'}>
        <span class="i">${t.icon || "🔗"}</span><div style="flex:1"><b>${esc(t.name)}</b>
        ${t.status === true ? ' <span class="dot on" title="responde"></span>' : t.status === false ? ' <span class="dot off" title="no responde"></span>' : ""}
        <div class="muted small">${esc(t.desc || "")}</div></div>
        ${t.home && ADMIN ? `<button class="small" data-home-off="${esc(t.home)}" title="Apagar (sus datos se quedan)">⏻</button>` : ""}</a>`;
    $("#tools").innerHTML = sections.map(([g, ts]) => `<div class="section-title">${esc(g)}</div><div class="links">${ts.map(tile).join("")}</div>`).join("")
      + (ADMIN ? `<p class="muted small" style="margin-top:16px">¿Falta algo? Agrega enlaces en Ajustes → editor avanzado (sección <span class="mono">links</span>).</p>` : "");
    const homeToggle = async (name, on, b) => {
      if (!on && !confirm("¿Apagarlo? Sus datos se quedan; lo vuelves a encender cuando quieras.")) return;
      b.disabled = true;
      try { await api("/api/home/toggle", { method: "POST", body: JSON.stringify({ name, on }) });
        toast(on ? "Encendiendo: tarda 1–3 min (la primera vez descarga la imagen). Resultado en Sistema." : "Apagando…"); }
      catch (e) { toast(e.message); b.disabled = false; }
    };
    $("#tools").querySelectorAll("[data-home-on]").forEach((b) => b.onclick = () => homeToggle(b.dataset.homeOn, true, b));
    $("#tools").querySelectorAll("[data-home-off]").forEach((b) => b.onclick = (e) => { e.preventDefault(); homeToggle(b.dataset.homeOff, false, b); });
  },

  async inicioFamilia() {
    view.innerHTML = `<div class="toolbar"><h2 class="grow">Hola${ME ? ", " + esc(ME.user) : ""} 👋</h2></div>
      <div class="grid" id="kpis"></div>
      <div class="section-title">Atajos</div>
      <div class="links">
        <a class="link" href="#respaldos"><span class="i">📦</span><div><b>Mis respaldos</b><div class="muted small">Tus equipos, subir fotos y archivos</div></div></a>
        <a class="link" href="#camaras"><span class="i">📷</span><div><b>Cámaras</b><div class="muted small">Ver la casa</div></div></a>
        <a class="link" href="#servicios"><span class="i">🧩</span><div><b>Servicios</b><div class="muted small">¿Funciona todo?</div></div></a>
        <a class="link" href="#cuenta"><span class="i">👤</span><div><b>Mi cuenta</b><div class="muted small">Cambiar tu contraseña</div></div></a>
      </div>`;
    const [b, v] = await Promise.all([api("/api/my/backups").catch(() => null), api("/api/services").catch(() => null)]);
    const last = b ? Math.max(0, ...b.devices.map((d) => d.last || 0)) : 0;
    $("#kpis").innerHTML = `
      <a class="card kpi" href="#respaldos"><span class="l">Tu último respaldo</span><span class="v" style="font-size:22px">${last ? ago(last) : "nunca"}</span>
        <span class="muted small">${b ? b.devices.length + " equipo(s)" : ""}</span></a>
      <a class="card kpi" href="#respaldos"><span class="l">Avisos nuevos</span><span class="v">${b ? b.notices.filter((n) => !n.seen).length : "—"}</span><span class="muted small">en Mis respaldos</span></a>
      <a class="card kpi" href="#respaldos"><span class="l">Telegram</span><span class="v" style="font-size:22px">${b?.telegram ? "✅" : "—"}</span><span class="muted small">${b?.telegram ? "vinculado" : "sin vincular"}</span></a>
      <a class="card kpi" href="#servicios"><span class="l">Servicios</span><span class="v">${v?.ok ? `${v.up}/${v.monitors.length}` : "—"}</span><span class="muted small">${v?.ok ? (v.down ? v.down + " caído(s)" : "todo arriba") : ""}</span></a>`;
  },

  async respaldos() {
    const j = await api("/api/my/backups");
    const OSI = { windows: "🪟", linux: "🐧", macos: "🍎", web: "🌐", telegram: "✈", android: "📱" };
    const unread = j.notices.filter((n) => !n.seen).length;
    const devCard = (d) => `<div class="card dev" data-dev="${esc(d.name)}">
      <h3><span style="font-size:20px">${OSI[d.os] || "💾"}</span> <span style="color:var(--text)">${esc(d.label || d.name)}</span>
        <span class="pill">${esc(d.name)}</span> ${d.last ? `<span class="pill ok">último ${ago(d.last)}</span>` : '<span class="pill warn">sin respaldos</span>'}</h3>
      <p class="small muted">${d.count ? `${d.count} archivo(s) · ${bytes(d.size)}` : "Todavía no llega nada."}
        ${d.clave && !["web", "telegram"].includes(d.os) ? ` · contraseña para restaurar: <span class="mono" data-clave="${esc(d.clave)}">••••••</span> <a href="#" data-show>ver</a>` : ""}</p>
      ${d.setup ? setupBox(d) : ""}
      ${d.files?.length && j.telegram && !d.legacy ? `<div class="toolbar" style="margin:6px 0"><button data-sendall>📲 Mandarme ${d.files.length > 1 ? `los ${d.files.length} respaldos en .zip` : "el respaldo"} por Telegram</button></div>` : ""}
      ${d.files?.length ? `<details><summary class="small">Archivos (${d.files.length})</summary><div class="table-wrap"><table>
        ${d.files.map((f) => `<tr><td class="small muted nowrap">n.º ${f.id}</td><td class="small ellipsis" style="max-width:300px" title="${esc(f.name)}">${esc(f.name)}</td><td class="small nowrap">${bytes(f.size)}</td>
          <td class="small nowrap">${ago(f.ts)}</td><td class="nowrap">${d.legacy ? "" : `${j.telegram ? `<button title="Mandármelo por Telegram" data-sendfile="${f.id}">📲</button>` : ""}
          <a href="/api/my/devices/${encodeURIComponent(d.name)}/files/${f.id}" download><button title="Descargar">⬇</button></a>
          <button title="Borrar de la Pi" data-rmfile="${f.id}">🗑</button>`}</td></tr>`).join("")}</table></div></details>` : ""}
      ${d.legacy || ["web", "telegram"].includes(d.os) ? "" : `<div class="toolbar" style="margin:10px 0 0">
        ${d.setup ? "" : '<button data-newlink>🔗 Nuevo enlace de instalación</button>'}
        <button class="danger" data-delete>Quitar equipo</button></div>`}</div>`;
    const setupBox = (d) => d.os === "windows"
      ? `<div class="banner info"><b>Instalar en la computadora Windows</b> (enlace válido hasta ${fmtTime(d.setup.expires)}):<ol style="margin:6px 0 0 18px;padding:0">
          <li>En esa computadora abre este dashboard y descarga: <a href="${esc(d.setup.windows)}"><b>Respaldo-${esc(d.name)}.cmd</b></a></li>
          <li>Ábrelo con doble clic. Si Windows dice «protegió su PC»: <i>Más información → Ejecutar de todas formas</i>.</li>
          <li>Listo: hace el primer respaldo y luego se repite solo cada 4 semanas.</li></ol></div>`
      : `<div class="banner info"><b>Instalar en ${d.os === "macos" ? "la Mac" : "la computadora Linux"}</b> (válido hasta ${fmtTime(d.setup.expires)}):
          abre la <b>Terminal</b> en esa computadora, pega esto y presiona Enter:
          <pre class="mono small" style="user-select:all;white-space:pre-wrap;margin:8px 0">${esc(d.setup.unix)}</pre>
          <button data-copy="${esc(d.setup.unix)}">Copiar</button> Hace el primer respaldo y luego se repite solo cada 4 semanas.</div>`;
    const linked = j.telegram;
    view.innerHTML = `<div class="toolbar"><h2 class="grow">Mis respaldos</h2></div>
      <div class="grid2">
        <div class="card"><h3>📤 Subir desde este equipo</h3>
          <p class="small muted">Para el <b>celular</b> (Android o iPhone) o cualquier computadora, sin instalar nada: elige fotos, videos o documentos y se guardan en la Pi.</p>
          <div class="toolbar" style="margin:0">
            <label class="btn primary"><input type="file" id="upf" multiple hidden>📷 Elegir fotos y archivos</label>
            <label class="btn"><input type="file" id="upd" webkitdirectory multiple hidden>📁 Elegir una carpeta</label></div>
          <div id="upbox"></div>
          <p class="small muted" style="margin-top:8px">Se borran solos después de ${j.max_age_days || "—"} días: descarga lo que quieras conservar.</p></div>
        <div class="card"><h3>➕ Respaldar una computadora automáticamente</h3>
          <p class="small muted">Elige su sistema: te doy un instalador ya configurado. Respalda Documentos, Escritorio e Imágenes, cifrado con una contraseña que solo tú ves aquí.</p>
          <div class="toolbar" style="margin:0"><input id="dlabel" placeholder="nombre, ej. laptop de mamá">
            <button data-add="windows">🪟 Windows</button><button data-add="linux">🐧 Linux</button><button data-add="macos">🍎 Mac</button></div></div>
      </div>
      <div class="section-title">Tus equipos</div>
      <div class="grid2" id="devs">${j.devices.map(devCard).join("") || '<div class="card muted">Aún no tienes equipos. Sube algo desde aquí o agrega una computadora.</div>'}</div>
      <div class="grid2" style="margin-top:var(--gap)">
        <div class="card"><h3>📲 Cuando llega un respaldo</h3>
          <label class="check"><input type="checkbox" id="pf-tg" ${j.prefs.send_telegram ? "checked" : ""} ${linked ? "" : "disabled"}>
            Mandármelo por Telegram ${linked ? "" : '<span class="muted small">(primero vincula tu Telegram)</span>'}</label>
          <p class="small muted" style="margin:2px 0 8px 26px">Si pesa más de 2 GB llega en partes (.001, .002…) que se juntan con 7-Zip o ZArchiver.</p>
          <label class="check"><input type="checkbox" id="pf-del" ${j.prefs.delete_after ? "checked" : ""}>
            Borrarlo de la Pi cuando ya lo tenga</label>
          <p class="small muted" style="margin:2px 0 0 26px">${linked && j.prefs.send_telegram
            ? "Se borra en cuanto te llega a Telegram. Ahí queda tu copia: no borres ese chat."
            : "Se borra cuando lo descargas completo aquí (⬇). Mientras, siempre puedes descargarlo."}</p></div>
        <div class="card"><h3>✈ Avisos por Telegram</h3>
          ${linked ? `<p>✅ Vinculado: <b>${esc(linked.name || "tu Telegram")}</b>. Ahí te llegan los avisos de <b>tus</b> respaldos${LOGS ? " y las alertas del homelab" : ""}, y puedes mandarle fotos y archivos al bot para guardarlos.</p>
              <button class="danger" id="tgoff">Desvincular</button>`
            : `<p class="small muted">Opcional. Si no usas Telegram, los avisos te aparecen aquí abajo.</p>
              <button class="primary" id="tgon">Vincular mi Telegram</button><div id="tgbox"></div>`}</div>
        <div class="card"><h3>🔔 Avisos ${unread ? `<span class="pill warn">${unread} nuevo(s)</span>` : ""}</h3>
          ${j.notices.length ? j.notices.map((n) => `<div class="ev"><span>${n.level === "ok" ? "✅" : n.level === "warning" ? "⚠" : "ℹ"}</span>
            <span class="t">${fmtTime(n.ts)}</span><div class="${n.seen ? "muted" : ""}">${esc(n.text)}</div></div>`).join("") : '<p class="muted">Sin avisos todavía.</p>'}</div>
      </div>`;
    if (unread) api("/api/my/notices/seen", { method: "POST" }).then(() => setUnread(0)).catch(() => {});
    // --- equipos
    view.querySelectorAll("[data-add]").forEach((b) => b.onclick = async () => {
      try {
        await api("/api/my/devices", { method: "POST", body: JSON.stringify({ os: b.dataset.add, label: $("#dlabel").value.trim() }) });
        toast("Equipo agregado: sigue las instrucciones"); views.respaldos();
      } catch (e) { toast(e.message); }
    });
    view.querySelectorAll(".dev").forEach((c) => {
      const name = c.dataset.dev;
      c.querySelector("[data-show]")?.addEventListener("click", (e) => {
        e.preventDefault(); const sp = c.querySelector("[data-clave]"); sp.textContent = sp.dataset.clave; e.target.remove();
      });
      c.querySelector("[data-newlink]")?.addEventListener("click", async () => {
        await api(`/api/my/devices/${encodeURIComponent(name)}/setup`, { method: "POST" }); views.respaldos();
      });
      c.querySelector("[data-delete]")?.addEventListener("click", async () => {
        if (!confirm(`¿Quitar ${name}? Ya no podrá subir respaldos.`)) return;
        const r = await api(`/api/my/devices/${encodeURIComponent(name)}`, { method: "DELETE" }); toast(r.note); views.respaldos();
      });
      c.querySelectorAll("[data-copy]").forEach((b) => b.onclick = () => navigator.clipboard?.writeText(b.dataset.copy).then(() => toast("Copiado")));
    });
    // --- cómo recibir los respaldos
    const savePrefs = async () => {
      try { await api("/api/my/prefs", { method: "POST", body: JSON.stringify({ send_telegram: $("#pf-tg").checked, delete_after: $("#pf-del").checked }) });
        toast("Guardado"); views.respaldos(); } catch (e) { toast(e.message); }
    };
    $("#pf-tg").onchange = savePrefs; $("#pf-del").onchange = savePrefs;
    const sendFile = (name, id) => api(`/api/my/devices/${encodeURIComponent(name)}/files/${id}/send`, { method: "POST" });
    view.querySelectorAll("[data-sendfile]").forEach((b) => b.onclick = async () => {
      try { const r = await sendFile(b.closest(".dev").dataset.dev, b.dataset.sendfile);
        b.disabled = true; toast(r.parts > 1 ? `Va en ${r.parts} partes: revisa Telegram en unos minutos` : "Enviando: revisa Telegram"); }
      catch (e) { toast(e.message); }
    });
    view.querySelectorAll("[data-sendall]").forEach((b) => b.onclick = async () => {
      const c = b.closest(".dev"), n = c.querySelectorAll("[data-sendfile]").length;
      if (!confirm(`¿Mandarte ${n} respaldo(s) por Telegram empacados en .zip?\n\nSi pasan de 2 GB llegan varios .zip; cada uno se abre por separado.`
        + ($("#pf-del")?.checked ? "\n\nComo tienes «Borrarlo de la Pi», se borran de la Pi cuando te lleguen." : ""))) return;
      b.disabled = true;
      try { const r = await api(`/api/my/devices/${encodeURIComponent(c.dataset.dev)}/send-zip`, { method: "POST", body: "{}" });
        toast(`Empacando ${r.files} respaldo(s) (${bytes(r.size)}) en ${r.zips > 1 ? r.zips + " .zip" : "un .zip"}: revisa Telegram`); }
      catch (e) { toast(e.message); b.disabled = false; }
    });
    view.querySelectorAll("[data-rmfile]").forEach((b) => b.onclick = async () => {
      const name = b.closest(".dev").dataset.dev;
      if (!confirm("¿Borrar este respaldo de la Pi? Asegúrate de tener tu copia.")) return;
      try { await api(`/api/my/devices/${encodeURIComponent(name)}/files/${b.dataset.rmfile}`, { method: "DELETE" });
        toast("Borrado de la Pi"); views.respaldos(); } catch (e) { toast(e.message); }
    });
    // --- Telegram
    $("#tgoff")?.addEventListener("click", async () => { await api("/api/my/telegram", { method: "DELETE" }); views.respaldos(); });
    $("#tgon")?.addEventListener("click", async () => {
      const r = await api("/api/my/telegram", { method: "POST" });
      const link = r.bot ? `https://t.me/${r.bot}?start=${r.code}` : "";
      $("#tgbox").innerHTML = `<div class="banner info" style="margin-top:10px">
        ${link ? `<p><a href="${esc(link)}" target="_blank" rel="noopener"><button class="primary">Abrir Telegram</button></a> y presiona <b>Iniciar</b>.</p><p class="small">O mándale al bot${r.bot ? ` <b>@${esc(r.bot)}</b>` : ""}:</p>` : "<p>Mándale al bot del homelab:</p>"}
        <pre class="mono" style="user-select:all;margin:4px 0">/vincular ${esc(r.code)}</pre><span class="small">El código sirve ${r.minutes} minutos.</span></div>`;
    });
    // --- subida desde el navegador
    const upload = async (files) => {
      if (!files.length) return;
      const box = $("#upbox");
      const { device, token } = await api("/api/my/web-token", { method: "POST" });
      const session = "web-" + Date.now(), total = [...files].reduce((s, f) => s + f.size, 0);
      let done = 0, ok = 0, fail = 0;
      const used = new Set();
      box.innerHTML = `<div style="margin-top:12px"><div class="progress"><i style="width:0%"></i></div>
        <p class="small" id="upmsg">Preparando ${files.length} archivo(s), ${bytes(total)}…</p></div>`;
      const bar = box.querySelector(".progress i"), msg = $("#upmsg");
      const put = (path, body, extra = {}) => new Promise((res) => {
        const x = new XMLHttpRequest();
        x.open("PUT", `/api/backup/${encodeURIComponent(device)}/${encodeURIComponent(path)}`);
        x.setRequestHeader("Authorization", "Bearer " + token);
        x.setRequestHeader("X-Backup-Session", session);
        Object.entries(extra).forEach(([k, v]) => x.setRequestHeader(k, v));
        x.upload.onprogress = (e) => { bar.style.width = ((done + e.loaded) / Math.max(1, total) * 100).toFixed(1) + "%"; };
        x.onload = () => res(x.status === 200); x.onerror = () => res(false);
        x.send(body);
      });
      for (const f of files) {
        let n = (f.webkitRelativePath || f.name).replaceAll("/", "__");
        for (let i = 2; used.has(n); i++) n = n.replace(/(\.[^.]*)?$/, `_${i}$1`);
        used.add(n);
        msg.textContent = `Subiendo ${ok + fail + 1} de ${files.length}: ${f.name}`;
        (await put(n, f, { "X-Backup-Batch": "1" })) ? ok++ : fail++;
        done += f.size;
      }
      await put("__fin__", "");
      bar.style.width = "100%";
      msg.innerHTML = fail ? `⚠ Subí ${ok} y fallaron ${fail}. Revisa tu conexión y vuelve a intentar con los que faltan.`
        : `✅ Listo: ${ok} archivo(s), ${bytes(total)} guardados en la Pi.`;
      setTimeout(() => views.respaldos(), 2500);
    };
    $("#upf").onchange = (e) => upload(e.target.files);
    $("#upd").onchange = (e) => upload(e.target.files);
  },

  async servicios() {
    const v = await api("/api/services");
    const hl = v.homelab || [];
    const own = hl.length ? `<div class="card"><h3>Del homelab <span class="muted small">(lo revisa el panel)</span></h3>${hl.map((t) => `
        <a class="svc" href="${esc(t.url)}" ${t.url.startsWith("#") ? "" : 'target="_blank" rel="noopener"'} style="color:inherit;text-decoration:none">
          <span class="dot ${t.status === true ? "on" : t.status === false ? "off" : ""}"></span>
          <div style="min-width:0"><b>${t.icon || ""} ${esc(t.name)}</b><div class="muted small ellipsis">${esc(t.desc || "")}</div></div>
          <div class="muted small" style="text-align:right">${t.status === true ? "responde" : t.status === false ? "no responde" : ""}</div></a>`).join("")}</div>` : "";
    if (!v.ok) {
      view.innerHTML = `<div class="toolbar"><h2 class="grow">Servicios</h2></div>
        <div class="grid2">${own}
        <div class="card"><h3>Uptime Kuma</h3><p>${esc(v.error)}</p>
        <ol class="muted"><li>Abre Uptime Kuma (puerto 3001) → <b>Páginas de estado</b> → <b>Nueva página de estado</b>.</li>
        <li>Nombre «Homelab», slug <span class="mono">homelab</span>.</li>
        <li>Agrega tus monitores y presiona <b>Guardar</b>.</li></ol></div></div>`;
      return;
    }
    const groups = {};
    v.monitors.forEach((m) => (groups[m.group || "Servicios"] ??= []).push(m));
    view.innerHTML = `<div class="toolbar"><h2 class="grow">Servicios <span class="muted small">${v.up}/${v.monitors.length} arriba en Uptime Kuma</span></h2>
        <a href="${esc(v.url.replace("127.0.0.1", location.hostname))}" target="_blank" rel="noopener"><button>Abrir Uptime Kuma ↗</button></a></div>
      <div class="grid2">${own}${Object.entries(groups).map(([g, ms]) => `<div class="card"><h3>${esc(g)}</h3>${ms.map((m) => `
        <div class="svc"><span class="dot ${m.status === 1 ? "on" : m.status === 0 ? "off" : "warn"}"></span>
          <div style="min-width:0"><b>${esc(m.name)}</b><div class="muted small ellipsis">${esc(m.status_name)}${m.ping != null && m.status === 1 ? ` · ${m.ping} ms` : ""}${m.msg && m.status !== 1 ? ` · ${esc(m.msg)}` : ""}</div></div>
          <div style="text-align:right"><div class="beats">${m.history.map((h) => `<i class="s${h}"></i>`).join("")}</div>
            <div class="muted small">${m.uptime_24h != null ? (m.uptime_24h * 100).toFixed(1) + "% en 24 h" : ""}</div></div></div>`).join("")}</div>`).join("")}</div>`;
  },

  async sistema() {
    const sy = await api("/api/system");
    const r = sy.revisar || {}, bk = sy.backup || {}, ap = sy.apply || {};
    const icon = { ok: "✅", warn: "⚠️", bad: "❌" };
    view.innerHTML = `<div class="toolbar"><h2 class="grow">Sistema</h2></div>
      ${ap.installed ? "" : '<div class="banner">Para que estos botones funcionen, corre una vez en la Pi: <span class="mono">sudo ./scripts/install-apply-service.sh</span></div>'}
      <div id="sysbox"></div>
      <div class="grid2">
        <div class="card"><h3>Acciones</h3>
          <div class="frow"><label>Actualizar</label><div><button class="primary" data-act="actualizar">⬇ Actualizar desde GitHub</button>
            <div class="muted small">git pull + reconstruye y reinicia lo que cambió. Igual que <span class="mono">./hl update</span>.</div></div></div>
          <div class="frow"><label>Revisar</label><div><button data-act="revisar">🩺 Revisar el homelab</button>
            <div class="muted small">Contenedores, firewall, SSH, disco, temperatura, bot… (<span class="mono">./hl check</span>)</div></div></div>
          <div class="frow"><label>Respaldo</label><div><button data-act="respaldo">🗄 Respaldar ahora</button>
            <div class="muted small">Cifrado; queda en tu SSD y te llega por Telegram. Cada domingo se hace solo.</div></div></div>
          <div class="frow"><label>Certificado</label><div>${sy.ca ? '<a href="/ca.crt"><button>⬇ Descargar certificado del homelab</button></a>' : '<span class="muted">Corre <span class="mono">./hl ca</span> en la Pi para poder descargarlo aquí.</span>'}
            <div class="muted small">Instálalo en tus equipos y el navegador deja de decir «no seguro» (docs/seguridad.md).</div></div></div>
        </div>
        <div class="card"><h3>🗄 Último respaldo del homelab</h3>
          ${bk.ts ? `<table class="kv"><tr><td>Cuándo</td><td>${fmtTime(bk.ts)} (${ago(bk.ts)})</td></tr>
            <tr><td>Resultado</td><td>${bk.ok ? "✅ bien" : "❌ " + esc(bk.error || "falló")}</td></tr>
            ${bk.size ? `<tr><td>Tamaño</td><td>${bytes(bk.size)}</td></tr>` : ""}
            ${bk.file ? `<tr><td>Archivo</td><td class="mono small" style="overflow-wrap:anywhere">${esc(bk.file)}</td></tr>` : ""}
            <tr><td>Copias</td><td>SSD ${(bk.sent || []).map((x) => "· " + esc(x)).join(" ")}</td></tr></table>`
            : '<p class="muted">Nunca se ha respaldado. Presiona «Respaldar ahora».</p>'}
          <p class="muted small">Restaurar en una Pi nueva: <span class="mono">./hl restore ARCHIVO</span></p></div>
      </div>
      <div class="card"><h3>🩺 Revisión ${r.ts ? `<span class="muted small">${ago(r.ts)}</span> <span class="pill ok">✔ ${r.ok}</span> <span class="pill warn">⚠ ${r.warn}</span> <span class="pill bad">✘ ${r.bad}</span>` : ""}</h3>
        ${r.ts ? `<div class="checks">${[...(r.items || [])].sort((a, b) => "bwo".indexOf(a.st[0]) - "bwo".indexOf(b.st[0])).map((i) => `<div class="row"><span>${icon[i.st]}</span>
          <div>${esc(i.what)}${i.fix ? `<div class="muted small mono">→ ${esc(i.fix)}</div>` : ""}</div></div>`).join("")}</div>`
          : '<p class="muted">Presiona «Revisar el homelab».</p>'}</div>`;
    const show = (st) => {
      const box = $("#sysbox"); if (!box) return;
      if (st.pending) box.innerHTML = '<div class="banner info">⏳ Trabajando… (si es «Actualizar», la página se reconecta sola)</div>';
      else if (st.log) box.innerHTML = `<div class="card" style="margin-bottom:var(--gap)"><h3>${st.ok ? "✅ Listo" : "⚠ Algo falló"} <span class="muted small">${ago(st.ts)}</span></h3>
        <details ${st.ok ? "" : "open"}><summary class="small">Ver bitácora</summary><pre class="mono small" style="white-space:pre-wrap;max-height:300px">${esc(st.log.join("\n"))}</pre></details></div>`;
    };
    if (ap.pending) show(ap);
    view.querySelectorAll("[data-act]").forEach((b) => b.onclick = async () => {
      if (b.dataset.act === "actualizar" && !confirm("¿Actualizar el homelab desde GitHub? Los servicios se reinician unos segundos.")) return;
      try {
        await api(`/api/system/${b.dataset.act}`, { method: "POST" });
        show({ pending: true });
        for (let i = 0; i < 150; i++) {
          await new Promise((r) => setTimeout(r, 2000));
          try { const st = await api("/api/apply"); if (!st.pending) { show(st); setTimeout(() => views.sistema(), 1500); return; } } catch { /* reiniciando */ }
        }
      } catch (e) { toast(e.message); }
    });
  },

  async cuentas() {
    const j = await api("/api/users");
    const roleSel = (r) => `<select data-role style="max-width:280px">${Object.entries(j.roles).map(([k, l]) => `<option value="${k}" ${k === r ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
    view.innerHTML = `<div class="toolbar"><h2 class="grow">Cuentas de la casa</h2></div>
      <div class="banner info">👨‍👩‍👧 <b>Familia</b>: solo sus respaldos, cámaras y servicios; los avisos de sus respaldos le llegan solo a esa persona.
        <b>Ve registros</b>: además ve dispositivos, tráfico, conexiones y eventos, y recibe las alertas del bot (sin poder cambiar nada).
        <b>Administrador</b>: todo. Te llega aviso por Telegram si alguien entra desde un equipo nuevo.</div>
      <div class="card flush"><table class="cards"><thead><tr><th>Usuario</th><th>Rol</th><th>Último acceso</th><th></th></tr></thead><tbody>
          ${j.users.map((u) => `<tr data-user="${esc(u.username)}"><td><b>${esc(u.username)}</b>${u.main ? ' <span class="pill ok">principal</span>' : ""}</td>
            <td data-l="Rol">${u.main ? "Administrador" : roleSel(u.role)}</td>
            <td data-l="Último acceso" class="small">${u.last_login ? ago(u.last_login) + (u.last_ip ? ` · <span class="mono">${esc(u.last_ip)}</span>` : "") : "nunca"}</td>
            <td>${u.main ? '<a href="#cuenta" class="small">cambiar contraseña</a>' : `<div class="acts"><button data-pw title="Nueva contraseña">🔑</button><button class="danger" data-del title="Borrar">🗑</button></div>`}</td></tr>`).join("")}
        </tbody></table></div>
      <div class="grid2">
        <div class="card"><h3>➕ Nueva cuenta</h3>
          <div class="frow"><label for="nu">Usuario</label><div><input id="nu" autocomplete="off" placeholder="mama, papa, hermana…"></div></div>
          <div class="frow"><label for="np">Contraseña</label><div><input id="np" type="password" autocomplete="new-password" placeholder="mínimo 8 caracteres"></div></div>
          <div class="frow"><label>Rol</label><div id="nr">${roleSel("familia")}</div></div>
          <button class="primary" id="nadd">Crear cuenta</button></div>

        <div class="card"><h3>Equipos desde los que han entrado</h3><div id="logins" class="muted">Cargando…</div></div>
      </div>`;
    $("#nadd").onclick = async () => {
      try {
        await api("/api/users", { method: "POST", body: JSON.stringify({ username: $("#nu").value.trim(), password: $("#np").value, role: $("#nr select").value }) });
        toast("Cuenta creada"); views.cuentas();
      } catch (e) { toast(e.message); }
    };
    view.querySelectorAll("tr[data-user]").forEach((tr) => {
      const u = tr.dataset.user;
      tr.querySelector("[data-role]")?.addEventListener("change", async (e) => {
        try { await api("/api/users", { method: "POST", body: JSON.stringify({ username: u, role: e.target.value }) }); toast("Rol cambiado"); }
        catch (er) { toast(er.message); }
      });
      tr.querySelector("[data-pw]")?.addEventListener("click", async () => {
        const pw = prompt(`Nueva contraseña para ${u} (mínimo 8):`);
        if (!pw) return;
        try { await api("/api/users", { method: "POST", body: JSON.stringify({ username: u, password: pw, role: tr.querySelector("[data-role]").value }) }); toast("Contraseña cambiada: su sesión se cerró"); }
        catch (er) { toast(er.message); }
      });
      tr.querySelector("[data-del]")?.addEventListener("click", async () => {
        if (!confirm(`¿Borrar la cuenta ${u}? Se cierra su sesión.`)) return;
        await api(`/api/users/${encodeURIComponent(u)}`, { method: "DELETE" }); views.cuentas();
      });
    });
    api("/api/users/logins").then((l) => {
      $("#logins").innerHTML = l.logins.length ? `<table><tr><th>Usuario</th><th>IP</th><th>Primera vez</th><th>Última</th></tr>${l.logins.map((x) =>
        `<tr><td>${esc(x.username)}</td><td class="mono">${esc(x.ip)}</td><td class="small">${fmtTime(x.first)}</td><td class="small">${ago(x.last)}</td></tr>`).join("")}</table>`
        : "Nadie ha entrado todavía.";
      const t = $("#logins table"); if (t) { const w = document.createElement("div"); w.className = "table-wrap"; t.before(w); w.append(t); }
    });
  },

  async cuenta() {
    view.innerHTML = `<div class="toolbar"><h2 class="grow">Mi cuenta</h2></div>
      <div class="grid2"><div class="card"><h3>👤 ${esc(ME?.user || "")} · ${esc(ME?.role_name || "")}</h3>
        <div class="frow"><label for="pc">Contraseña actual</label><div><input id="pc" type="password" autocomplete="current-password"></div></div>
        <div class="frow"><label for="pn">Nueva</label><div><input id="pn" type="password" autocomplete="new-password" placeholder="mínimo 8 caracteres"></div></div>
        <div class="frow"><label for="pn2">Repítela</label><div><input id="pn2" type="password" autocomplete="new-password"></div></div>
        <button class="primary" id="psave">Cambiar contraseña</button>
        <p class="muted small" style="margin-top:10px">Al cambiarla se cierran tus sesiones en otros equipos.</p></div>
        <div class="card"><h3>Sesión</h3><p class="muted">La sesión dura 7 días en este navegador.</p>
          <form method="post" action="/auth/logout"><button type="submit">⎋ Cerrar sesión</button></form></div></div>`;
    $("#psave").onclick = async () => {
      if ($("#pn").value !== $("#pn2").value) return toast("Las contraseñas nuevas no coinciden");
      try {
        await api("/api/me/password", { method: "POST", body: JSON.stringify({ current: $("#pc").value, new: $("#pn").value }) });
        toast("✅ Contraseña cambiada"); ["#pc", "#pn", "#pn2"].forEach((id) => { $(id).value = ""; });
      } catch (e) { toast(e.message); }
    };
  },

  async ajustes() {
    const s = await api("/api/settings");
    const field = (f) => {
      const id = `f_${f.key || f.path.replace(/\./g, "_")}`;
      const data = f.key ? `data-env="${f.key}"` : `data-yaml="${f.path}"`;
      let input;
      if (f.kind === "secret" || f.kind === "password") {
        input = `<input type="password" id="${id}" ${data} placeholder="${f.set ? esc(f.hint || "guardado") + " · escribe para cambiar" : "sin configurar"}" autocomplete="new-password">
          ${f.generate ? `<label class="small"><input type="checkbox" data-gen="${f.key}"> generar uno nuevo</label>` : ""}`;
      } else if (f.kind === "profiles") {
        input = Object.entries(f.options).map(([k, label]) => `<label class="chk"><input type="checkbox" data-profile="${k}" ${f.value.includes(k) ? "checked" : ""}> <b>${k}</b> — ${esc(label)}</label>`).join("");
      } else if (f.kind === "select") {
        input = `<select id="${id}" ${data}>${Object.entries(f.options).map(([k, l]) => `<option value="${esc(k)}" ${k === (f.value ?? "") ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
      } else if (f.kind === "bool") {
        const on = f.value === true || f.value === "true";
        input = `<label class="chk"><input type="checkbox" id="${id}" ${data} data-bool="1" ${on ? "checked" : ""}> activado</label>`;
      } else {
        input = `<input id="${id}" ${data} value="${esc(f.value ?? "")}" ${f.kind === "number" ? 'inputmode="numeric"' : ""}>`;
      }
      return `<div class="frow"><label for="${id}">${esc(f.label)}</label><div>${input}${f.help ? `<div class="muted small">${esc(f.help)}</div>` : ""}</div></div>`;
    };
    const card = (title, fields) => `<div class="card"><h3>${esc(title)}</h3>${fields.map(field).join("")}</div>`;
    const ap = s.apply;
    view.innerHTML = `<div class="toolbar"><h2 class="grow">Ajustes</h2>
        <button id="save">💾 Guardar</button><button class="primary" id="saveapply">💾 Guardar y aplicar</button></div>
      ${s.env_exists ? "" : '<div class="banner">No encuentro el .env del homelab montado en el contenedor. Actualiza (git pull) y levanta de nuevo.</div>'}
      ${ap.installed ? "" : '<div class="banner">Para que «aplicar» funcione desde aquí, corre una vez en la Pi: <span class="mono">sudo ./scripts/install-apply-service.sh</span>. Mientras, guarda y aplica a mano con <span class="mono">docker compose … up -d</span>.</div>'}
      <div id="applybox"></div>
      <div class="grid2">${Object.entries(s.env_groups).map(([g, fs]) => card(g, fs)).join("")}
        ${Object.entries(s.yaml_groups).map(([g, fs]) => card(g + " (homelab.yml)", fs)).join("")}
        <div class="card"><h3>Equipos que respaldan</h3>
          <table id="devs"><tr><th>Nombre</th><th>Sistema</th><th>Token</th><th></th></tr>
          ${s.devices.map((d) => devRow(d)).join("")}</table>
          <button id="adddev" style="margin-top:8px">＋ Agregar equipo</button>
          <div class="muted small">Al guardar, cada equipo nuevo recibe su token. Cópialo a su cliente de respaldo.</div>
          <div id="toks"></div></div>
      </div><br>
      <div class="card"><h3>Editor avanzado: homelab.yml</h3>
        <p class="muted small">Todo lo demás (redes, access points, cámaras, enlaces…). Se valida antes de guardar y queda copia en run/.</p>
        <textarea id="yraw" spellcheck="false" style="width:100%;height:340px" class="mono">${esc(s.yaml_raw)}</textarea>
        <div class="toolbar" style="margin-top:8px"><button id="ysave">💾 Guardar YAML</button><button class="primary" id="ysaveapply">💾 Guardar YAML y aplicar</button></div></div>`;
    function devRow(d) {
      return `<tr data-dev><td><input value="${esc(d.name)}" data-dname style="min-width:120px"></td>
        <td><select data-dos>${["linux", "windows", "macos", "android", "ios"].map((o) => `<option ${o === d.os ? "selected" : ""}>${o}</option>`).join("")}</select></td>
        <td>${d.token_set ? '<span class="pill ok">guardado</span>' : '<span class="pill">nuevo</span>'}</td>
        <td style="white-space:nowrap">${d.token_set ? `<button data-regen="${esc(d.name)}" title="Generar otro (el viejo deja de servir)">↻</button>` : ""}<button data-del>🗑</button></td></tr>`;
    }
    const showApply = (st) => {
      if (!st) return;
      const box = $("#applybox");
      if (st.pending) box.innerHTML = '<div class="banner">⏳ Aplicando cambios… (la página puede reconectarse sola)</div>';
      else if (st.log) box.innerHTML = `<div class="card"><h3>${st.ok ? "✅ Cambios aplicados" : "⚠ Algo falló al aplicar"} <span class="muted small">${ago(st.ts)}</span></h3>
        <details ${st.ok ? "" : "open"}><summary class="small">Ver bitácora</summary><pre class="mono small" style="white-space:pre-wrap;max-height:260px;overflow:auto">${esc(st.log.join("\n"))}</pre></details></div>`;
    };
    showApply(ap);
    const poll = async () => {
      for (let i = 0; i < 90; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        try { const st = await api("/api/apply"); showApply(st); if (!st.pending) return; } catch { /* netwatch reiniciando */ }
      }
    };
    const collect = () => {
      const env = {}, yaml = {}, generate = {};
      view.querySelectorAll("[data-env]").forEach((el) => { env[el.dataset.env] = el.dataset.bool ? el.checked : el.value; });
      const prof = [...view.querySelectorAll("[data-profile]")].filter((c) => c.checked).map((c) => c.dataset.profile);
      if (view.querySelector("[data-profile]")) env.COMPOSE_PROFILES = prof;
      view.querySelectorAll("[data-yaml]").forEach((el) => { yaml[el.dataset.yaml] = el.dataset.bool ? el.checked : el.value; });
      view.querySelectorAll("[data-gen]").forEach((el) => { if (el.checked) generate[el.dataset.gen] = true; });
      const devices = [...view.querySelectorAll("tr[data-dev]")].map((tr) => ({ name: tr.querySelector("[data-dname]").value, os: tr.querySelector("[data-dos]").value }));
      return { env, yaml, generate, devices };
    };
    const apply = async (restart) => {
      const services = restart === "all" ? "all" : restart.filter((x) => x !== "*");
      const force = restart === "all" ? ["netwatch", "bot"] : restart.filter((x) => ["netwatch", "bot", "caddy"].includes(x));
      await api("/api/apply", { method: "POST", body: JSON.stringify({ services: services.length ? services : "all", force }) });
      showApply({ pending: true }); poll();
    };
    const save = async (andApply) => {
      try {
        const r = await api("/api/settings", { method: "POST", body: JSON.stringify(collect()) });
        const toks = Object.entries(r.new_tokens || {});
        if (toks.length) $("#toks").innerHTML = `<div class="banner">Tokens nuevos (cópialos ahora, no se vuelven a mostrar):<br>${toks.map(([d, t]) => `<b>${esc(d)}</b>: <span class="mono" style="user-select:all">${esc(t)}</span>`).join("<br>")}</div>`;
        const n = r.env_changed.length + (r.yaml_changed ? 1 : 0);
        toast(n ? `Guardado (${n} cambio${n > 1 ? "s" : ""})` : "No había cambios");
        if (r.dns_msg) setTimeout(() => toast(`DNS de salida: ${r.dns_msg}`), 2600);
        if (andApply && n) await apply(r.restart);
        else if (n && !toks.length) setTimeout(() => views.ajustes(), 600);
      } catch (e) { toast(e.message); }
    };
    $("#save").onclick = () => save(false);
    $("#saveapply").onclick = () => save(true);
    $("#adddev").onclick = () => $("#devs").insertAdjacentHTML("beforeend", devRow({ name: "", os: "linux", token_set: false }));
    $("#devs").addEventListener("click", async (e) => {
      const b = e.target.closest("button"); if (!b) return;
      if (b.dataset.del !== undefined) b.closest("tr").remove();
      if (b.dataset.regen && confirm(`¿Generar otro token para ${b.dataset.regen}? El actual deja de funcionar en ese equipo.`)) {
        const r = await api("/api/settings/token", { method: "POST", body: JSON.stringify({ device: b.dataset.regen }) });
        $("#toks").innerHTML = `<div class="banner">Token nuevo de <b>${esc(r.device)}</b> (cópialo ahora): <span class="mono" style="user-select:all">${esc(r.token)}</span></div>`;
        await apply(["bot"]);
      }
    });
    const ysave = async (andApply) => {
      try {
        await api("/api/settings/yaml", { method: "POST", body: JSON.stringify({ raw: $("#yraw").value }) });
        toast("homelab.yml guardado");
        if (andApply) await apply(["netwatch", "bot"]);
      } catch (e) { toast(e.message); }
    };
    $("#ysave").onclick = () => ysave(false);
    $("#ysaveapply").onclick = () => ysave(true);
    if (ap.pending) poll();
  },

  async vpn() {
    const j = await api("/api/vpn");
    const hsAgo = (t) => (t ? ago(t) : "nunca");
    if (!j.interfaces.length) {
      view.innerHTML = `<h2>VPN</h2><div class="card"><p>No encontré túneles WireGuard activos en la Pi.</p>
        <p class="muted">${esc(j.error || "")}</p><p class="muted small">Ver docs/vpn.md.</p></div>`;
      return;
    }
    view.innerHTML = `<h2>VPN (WireGuard)</h2>${j.interfaces.map((i) => {
      const h = i.hub;
      return `<div class="card" style="margin-bottom:14px"><h3>🔐 ${esc(i.name)} · ${esc(i.addresses.join(", ") || "sin IP")}
          ${i.listen_port ? `<span class="pill">puerto ${i.listen_port}</span>` : ""}</h3>
        ${h.warnings.map((w) => `<div class="banner">${esc(w)}</div>`).join("")}
        <table><tr><td class="muted">Red del túnel</td><td class="mono">${esc(h.network || "—")}</td></tr>
          <tr><td class="muted">Esta Pi</td><td class="mono">${esc(h.my_ip || "—")}</td></tr>
          <tr><td class="muted">VPS (detectado)</td><td class="mono">${esc(h.hub_ip || "—")} ← ${esc(h.hub_endpoint || "")}</td></tr>
          <tr><td class="muted">Monitoreo del VPS</td><td>${h.monitored ? '<span class="pill ok">en Grafana y alertas</span>'
            : h.hub_ip ? `<button data-mon="${esc(i.name)}">📈 Monitorear el VPS (${esc(h.hub_ip)})</button>` : "—"}</td></tr></table>
        <h3 style="margin-top:14px">Peers</h3><table><tr><th>Nombre</th><th>Llave</th><th>Endpoint</th><th>IPs permitidas</th><th>Último contacto</th><th>↓ / ↑</th></tr>
          ${i.peers.map((p) => `<tr><td>${esc(p.name || "—")}</td><td class="mono small">${esc(p.public_key.slice(0, 10))}…</td>
            <td class="mono small">${esc(p.endpoint || "—")}</td><td class="mono small">${esc(p.allowed_ips.join(", "))}</td>
            <td><span class="dot ${p.handshake && j.now - p.handshake < 300 ? "on" : "off"}"></span> ${hsAgo(p.handshake)}</td>
            <td class="small">${bytes(p.rx)} / ${bytes(p.tx)}</td></tr>`).join("")}</table></div>`;
    }).join("")}
    <div class="grid2">
      <div class="card"><h3>➕ Agregar un equipo (teléfono, laptop…)</h3>
        <div class="frow"><label>Nombre</label><div><input id="vname" placeholder="telefono"></div></div>
        <div class="frow"><label>Túnel</label><div><select id="viface">${j.interfaces.map((i) => `<option>${esc(i.name)}</option>`).join("")}</select></div></div>
        <div class="frow"><label>Interfaz en el VPS</label><div><input id="vvps" placeholder="igual que en la Pi">
          <div class="muted small">Cómo se llama el túnel en el VPS (sudo wg show allá).</div></div></div>
        <div class="frow"><label>DNS</label><div><label class="chk"><input type="checkbox" id="vdns" checked> usar el AdGuard de casa (filtro también fuera de casa)</label></div></div>
        <button class="primary" id="vnew">Crear</button>
        <div id="vout"></div></div>
      <div class="card"><h3>Equipos creados aquí</h3>
        <table><tr><th>Nombre</th><th>IP</th><th>Creado</th><th></th></tr>
        ${j.clients.map((c) => `<tr><td>${esc(c.name)}</td><td class="mono">${esc(c.ip)}</td><td class="small">${ago(c.created)}</td>
          <td><button class="danger" data-cdel="${c.id}">Quitar</button></td></tr>`).join("") || '<tr><td colspan="4" class="muted">Ninguno todavía.</td></tr>'}</table>
        <div id="vdel"></div></div></div>`;
    view.querySelectorAll("[data-mon]").forEach((b) => b.onclick = async () => {
      const r = await api("/api/vpn/monitor", { method: "POST", body: JSON.stringify({ iface: b.dataset.mon }) });
      b.outerHTML = `<span class="pill ok">agregado</span><div class="small">${r.applied ? "Aplicando (netwatch se reinicia)…" : "Aplica desde Ajustes."}<br>${esc(r.vps_steps)}</div>`;
    });
    $("#vnew").onclick = async () => {
      try {
        const r = await api("/api/vpn/client", { method: "POST", body: JSON.stringify({
          name: $("#vname").value, iface: $("#viface").value, vps_iface: $("#vvps").value, dns: $("#vdns").checked }) });
        const blob = URL.createObjectURL(new Blob([r.config], { type: "text/plain" }));
        $("#vout").innerHTML = `<div class="banner" style="margin-top:10px">⚠ Esta configuración tiene la llave privada: se muestra <b>una sola vez</b>. Escanéala o descárgala ahora.</div>
          <p><b>1. En el equipo:</b> app WireGuard → «+» → escanear QR, o importa el archivo.</p>
          ${r.qr ? `<div style="background:#fff;display:inline-block;padding:8px;border-radius:8px;width:240px">${r.qr}</div>` : ""}
          <p><a href="${blob}" download="${esc(r.filename)}"><button>⬇ Descargar ${esc(r.filename)}</button></a></p>
          <details><summary class="small">Ver configuración</summary><pre class="mono small" style="white-space:pre-wrap">${esc(r.config)}</pre></details>
          <p><b>2. En el VPS</b> (para que acepte a «${esc(r.name)}», IP ${esc(r.ip)}):</p>
          <pre class="mono small" style="white-space:pre-wrap;user-select:all">${esc(r.vps_commands)}</pre>
          <p class="muted small">Pruébalo con datos móviles: activa el túnel y abre https://${esc(j.interfaces.find((i) => i.name === $("#viface").value)?.hub.my_ip || "IP-de-la-Pi")}:8443</p>`;
      } catch (e) { toast(e.message); }
    };
    view.querySelectorAll("[data-cdel]").forEach((b) => b.onclick = async () => {
      if (!confirm("¿Quitarlo de la lista? Después corre en el VPS el comando que te muestro.")) return;
      const r = await api(`/api/vpn/client/${b.dataset.cdel}`, { method: "DELETE" });
      b.closest("tr").remove();
      $("#vdel").innerHTML = `<pre class="mono small" style="white-space:pre-wrap;user-select:all">${esc(r.vps_commands)}</pre>`;
    });
  },

  async resumen() {
    const [s, ev] = await Promise.all([api("/api/summary"), api("/api/events?limit=8")]);
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
        ${s.ups?.enabled ? `<div class="card kpi"><div class="l">No-break</div><div class="v" style="color:${!s.ups.ok || s.ups.on_battery ? "var(--warn)" : "inherit"}">${s.ups.ok ? (s.ups.charge != null ? Math.round(s.ups.charge) + "%" : "✅") : "—"}</div><div class="muted small">${s.ups.ok ? esc(s.ups.status_text) + (s.ups.runtime ? ` · ${Math.round(s.ups.runtime / 60)} min de batería` : "") : "sin conexión con NUT"}</div></div>` : ""}
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
        ${(d.upstream || []).length ? `<p class="small muted">Sale por: ${d.upstream.map((u) => esc(u.replace(/^https:\/\//, ""))).join(", ")}</p>` : ""}
        ${d.error ? `<p class="muted">${esc(d.error)}</p>` : `<p>${d.queries} consultas · <b style="color:var(--warn)">${d.blocked}</b> bloqueadas · <b style="color:var(--bad)">${d.malware}</b> malware/phishing</p>
        <table><tr><th>Equipo</th><th>Consultas</th></tr>${d.top_clients.slice(0, 8).map((c) => `<tr><td>${esc(c.name || c.ip)} <span class="muted small">${c.ip}</span></td><td>${c.n}</td></tr>`).join("")}</table>`}`;
      view.querySelector(".grid2")?.append(card);
    }).catch(() => {});
    api("/api/wol").then((w) => {
      if (!w.targets.length) return;
      const card = document.createElement("div"); card.className = "card";
      card.innerHTML = `<h3>⏻ Encender equipos</h3><table>${w.targets.map((t) =>
        `<tr><td><span class="dot ${t.online ? "on" : "off"}"></span></td><td>${esc(t.name)}</td>
         <td class="mono small">${esc(t.ip || t.mac)}</td>
         <td>${t.online ? '<span class="muted small">ya está prendido</span>' : `<button class="btn" data-wol="${esc(t.mac)}">Encender</button>`}</td></tr>`).join("")}</table>
        <p class="muted small">Necesita «Wake on LAN» activado en el BIOS del equipo y cable de red.</p>`;
      card.querySelectorAll("[data-wol]").forEach((b) => b.onclick = async () => {
        try { await api("/api/wol", { method: "POST", body: JSON.stringify({ mac: b.dataset.wol }) });
          b.textContent = "Señal enviada"; b.disabled = true; toast("Listo: en 1–2 min debería aparecer en línea"); }
        catch (e) { toast(e.message); }
      });
      view.querySelector(".grid2")?.prepend(card);
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
          <td class="st"><span class="dot ${d.online ? "on" : ""}" title="${d.online ? "en línea" : "desconectado"}"></span></td>
          <td class="namecell"><input class="nm" value="${esc(d.name || "")}" placeholder="${esc(d.display_name)}" aria-label="Nombre" ${ADMIN ? "" : "readonly"}>
            <div class="sub">${d.known ? "" : '<span class="new">NUEVO</span> '}${d.hostname ? esc(d.hostname) : ""}${d.hostname_src ? ` <span class="muted">· ${esc(d.hostname_src)}</span>` : ""}</div></td>
          <td data-l="Tipo"><select class="tp" ${ADMIN ? "" : "disabled"}>${j.types.map((t) => `<option ${t === d.display_type ? "selected" : ""}>${t}</option>`).join("")}</select>
            ${d.ports.length ? `<div class="sub mono" title="${esc(d.ports.join("\n"))}">${esc(d.ports.map((p) => p.split("/")[0]).slice(0, 6).join(", "))}${d.ports.length > 6 ? "…" : ""}</div>` : ""}</td>
          <td data-l="IP / red · visto" class="mono">${esc(d.ip)}<div class="sub">${esc(d.network || "")}${d.network ? " · " : ""}${ago(d.last_seen)}</div></td>
          <td data-l="MAC / fabricante" class="mono">${esc(d.mac)}${d.random_mac ? ' <span class="pill" title="MAC privada/aleatoria">privada</span>' : ""}
            <div class="sub ellipsis" style="max-width:200px" title="${esc(d.vendor || "")}">${esc(d.vendor || "")}</div></td>
          <td data-l="AP"><select class="ap" ${ADMIN ? "" : "disabled"}><option value="">—</option>${j.aps.map((a) => `<option ${a === d.ap ? "selected" : ""}>${esc(a)}</option>`).join("")}</select></td>
          <td data-l="Tráfico" class="small tr-col">${d.bytes_down != null ? "↓ " + bytes(d.bytes_down) + "<br>↑ " + bytes(d.bytes_up) : ""}</td>
          <td class="acts-c"><div class="acts">
            ${d.known || !ADMIN ? "" : '<button class="ok" title="Ya lo revisé">✔</button>'}
            ${ADMIN ? `<button class="scan" title="Escanear puertos">🔍</button>
            <button class="blk danger" title="Bloquear (ACL)">⛔</button>
            <button class="del danger" title="Olvidar">🗑</button>` : ""}</div></td>
        </tr>`).join("") || '<tr><td class="muted">Nada coincide con el filtro.</td></tr>';
    };
    view.innerHTML = `
      <div class="toolbar"><h2 class="grow">Dispositivos <span class="muted small">${j.devices.filter((d) => d.online).length} en línea · ${j.devices.length} conocidos</span></h2>
        <input id="flt" type="search" placeholder="Filtrar nombre, hostname, IP, MAC…">
        ${ADMIN ? '<button id="seek" title="Busca la IP, MAC o nombre en la red aunque no esté en la lista">🔎 Buscar en la red</button>' : ""}
        ${ADMIN ? '<button id="allknown">✔ Todos revisados</button>' : ""}</div>
      <div class="card flush"><div class="table-wrap"><table class="devtable cards"><thead><tr>
        <th data-k="online"></th><th data-k="display_name">Nombre / hostname</th><th data-k="display_type">Tipo / puertos</th><th data-k="ip">IP / red · visto</th>
        <th data-k="mac">MAC / fabricante</th><th data-k="ap">AP</th><th data-k="bytes_down" class="tr-col">Tráfico</th><th class="acts-h"></th>
      </tr></thead><tbody id="tb"></tbody></table></div></div>
      <p class="muted small">El AP no se puede saber por ARP: asígnalo aquí y aparecerá en el mapa y en «Por AP». El tráfico por dispositivo solo existe en modo puente.</p>`;
    if (!j.devices.some((d) => d.bytes_down != null)) $(".devtable").classList.add("notraffic");
    draw();
    $("#flt").oninput = (e) => { filter = e.target.value.toLowerCase(); draw(); };
    view.querySelectorAll("th[data-k]").forEach((th) => th.onclick = () => {
      asc = sortKey === th.dataset.k ? !asc : true; sortKey = th.dataset.k; draw();
    });
    if ($("#seek")) $("#seek").onclick = async () => {
      const q = $("#flt").value.trim();
      if (!q) return toast("Escribe una IP, MAC o nombre en el filtro");
      const box = document.createElement("div");
      box.className = "card"; box.style.marginBottom = "12px";
      box.innerHTML = `<h3>🔎 Buscando «${esc(q)}» en la red… (ping, ARP, nombre y puertos; hasta 1 min)</h3>`;
      view.querySelector(".card.flush").before(box);
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
    if ($("#allknown")) $("#allknown").onclick = async () => { await api("/api/devices/known", { method: "POST" }); route(); };
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
      <input id="cq" type="search" placeholder="Filtrar IP, nombre, origen…">
      <select id="ca"><option value="">Todas</option><option value="permitida">Permitidas</option>
        <option value="bloqueada">Bloqueadas (UFW)</option></select>
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
          <div class="card kpi"><div class="l">Desde Internet</div><div class="v">${j.ips.filter((r) => r.origin === "Internet").length}</div><div class="muted small">IPs públicas</div></div>
        </div><br>
        <div class="card"><h3>Por hora</h3>${hourlyBars(j.hourly.filter((r) => !act || r.action === act), Math.min(hours, 168))}</div><br>
        <div class="card"><h3>Quién se conecta a qué <span class="muted small">(pasa el ratón; clic en una IP para el detalle)</span></h3>
          <div class="legend" style="margin:0 0 8px">${[["casa", "#5aa9ff"], ["laboratorio", "#2dd4bf"], ["otra red (vía Tenda)", "#f5a524"], ["VPN", "#c084fc"], ["Internet", "#f2555a"]]
            .map(([k, c]) => `<span><i style="background:${c};border-radius:50%"></i>${k}</span>`).join("")}</div>
          <div style="overflow-x:auto">${flowGraph(ips, act)}</div></div><br>
        <div class="grid2"><div class="card" style="overflow:auto"><h3>Detalle por IP</h3><table><tr><th>IP</th><th>Origen</th><th>Servicios</th><th>✔</th><th>⛔</th><th>Última</th></tr>
          ${ips.map((r) => `<tr class="iprow" data-ip="${r.ip}" style="cursor:pointer"><td class="mono">${esc(r.ip)}<div class="muted small">${esc(r.name)}</div></td>
            <td><span class="dot" style="background:${ORIGIN_COLOR(r.origin)}"></span> ${esc(r.origin)}</td>
            <td class="small">${Object.keys(r.ports).map((p) => esc(r.services[p])).join(", ")}</td>
            <td>${r.actions.permitida || ""}</td><td>${r.actions.bloqueada || ""}</td>
            <td class="small">${ago(r.last)}</td></tr>`).join("") || '<tr><td colspan="6" class="muted">Nada todavía.</td></tr>'}</table></div>
          <div class="card" id="ipd"><h3>Selecciona una IP</h3><p class="muted">Haz clic en una IP del grafo o de la tabla.</p></div></div>`;
      const showIp = async (ip) => {
        const d = await api(`/api/inbound/${ip}`);
        const r = j.ips.find((x) => x.ip === ip) || { ports: {}, services: {}, actions: {} };
        $("#ipd").innerHTML = `<h3>${esc(ip)} · ${esc(d.origin)}</h3>
          ${r.name ? `<p>Equipo: <b>${esc(r.name)}</b> <span class="mono small">${esc(r.mac)}</span></p>` : ""}
          <table><tr><th>Servicio</th><th>Permitidas</th><th>Bloqueadas</th></tr>${Object.entries(r.ports).map(([p, a]) =>
            `<tr><td>${esc(r.services[p])} <span class="muted">:${p}</span></td><td>${a.permitida || ""}</td><td>${a.bloqueada || ""}</td></tr>`).join("")}</table>
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
    const probeCard = ADMIN ? `<div class="card"><h3>🔎 Detectar cámara genérica (foco 360°, mini cámara WiFi)</h3>
        <p class="muted small">Pon la IP de la cámara y la contraseña del <b>dispositivo</b> que pusiste en su app (iCSee, V380, Yoosee, CamHi…).
          Pruebo las rutas RTSP conocidas y te doy el bloque para Frigate.</p>
        <div class="toolbar" style="margin:0">
          <input id="pip" placeholder="IP, ej. 192.168.1.40" inputmode="decimal">
          <input id="pname" placeholder="nombre, ej. foco_sala">
          <input id="puser" placeholder="usuario" value="admin" autocomplete="off">
          <input id="ppw" type="password" placeholder="contraseña" autocomplete="new-password">
          <button class="primary" id="pgo">Detectar</button></div>
        <div id="pout"></div></div>` : "";
    if (!j.cameras.length) {
      view.innerHTML = `<div class="toolbar"><h2 class="grow">Cámaras</h2></div>
        <div class="card"><p>Todavía no hay cámaras en Frigate.</p>
        <p class="muted">${esc(j.error || "")}</p><p class="muted small">Frigate corre en la PC Linux; revisa <span class="mono">cameras.frigate_url</span> y docs/camaras.md.</p></div>${probeCard}`;
    } else {
      view.innerHTML = `<div class="toolbar"><h2 class="grow">Cámaras</h2>
        <a href="${esc(j.frigate_url)}" target="_blank"><button>Abrir Frigate ↗</button></a></div>
        <div class="cams">${j.cameras.map((c) => `<div class="card cam"><h3>📷 ${esc(c)}</h3>
          <img data-cam="${esc(c)}" alt="${esc(c)}">
          <div class="toolbar" style="margin:10px 0 0">
            ${j.go2rtc_url ? `<a href="${esc(j.go2rtc_url)}/stream.html?src=${encodeURIComponent(c)}" target="_blank"><button>▶ En vivo</button></a>` : ""}
            <a href="${esc(j.frigate_url)}/review?cameras=${encodeURIComponent(c)}" target="_blank"><button>Grabaciones</button></a>
          </div></div>`).join("")}</div>${probeCard}`;
      const refresh = () => view.querySelectorAll("img[data-cam]").forEach((img) =>
        img.src = `/api/cameras/${img.dataset.cam}/snapshot?t=${Date.now()}`);
      refresh(); timer = setInterval(refresh, 5000);
    }
    if (!ADMIN) return;
    $("#pgo").onclick = async () => {
      const out = $("#pout"); out.innerHTML = '<p class="muted" style="margin-top:10px">Probando puertos y rutas… (hasta 1 min)</p>';
      $("#pgo").disabled = true;
      try {
        const r = await api("/api/cameras/probe", { method: "POST", body: JSON.stringify({ ip: $("#pip").value.trim(),
          name: $("#pname").value.trim(), user: $("#puser").value.trim(), password: $("#ppw").value }) });
        const f = r.found;
        out.innerHTML = `<div style="margin-top:12px">
          <p>Puertos abiertos: ${r.ports.map((p) => `<span class="pill">${p.port} ${esc(p.what)}</span>`).join(" ") || '<span class="muted">ninguno de los de cámara</span>'}</p>
          ${f ? `<div class="banner okb">✅ Es de la familia <b>${esc(f.family)}</b> (app ${esc(f.app)}).</div>
              <table class="kv"><tr><td>HD (grabar)</td><td class="mono small" style="overflow-wrap:anywhere">${esc(f.main_masked)}</td></tr>
              ${f.sub_masked ? `<tr><td>SD (detectar)</td><td class="mono small" style="overflow-wrap:anywhere">${esc(f.sub_masked)}</td></tr>` : ""}</table>
              ${f.note ? `<p class="muted small">${esc(f.note)}</p>` : ""}
              <p class="small" style="margin-top:10px">Pega esto en <span class="mono">config/frigate/config.yml</span> de la PC:</p>
              <pre class="mono small" style="user-select:all;white-space:pre">${esc(r.frigate)}</pre>
              <button id="pcopy">Copiar</button>` : ""}
          ${r.notes.map((n) => `<div class="banner">${esc(n)}</div>`).join("")}
          ${!f && r.tried.length ? `<details><summary class="small">Rutas probadas (${r.tried.length})</summary><pre class="mono small">${esc(r.tried.map((t) => `${t.result}  ${t.family}  ${t.path}`).join("\n"))}</pre></details>` : ""}</div>`;
        $("#pcopy")?.addEventListener("click", () => navigator.clipboard?.writeText(r.frigate).then(() => toast("Copiado")));
      } catch (e) { out.innerHTML = `<div class="banner" style="margin-top:10px">${esc(e.message)}</div>`; }
      $("#pgo").disabled = false;
    };
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

  async inventario() {
    const j = await api("/api/inventory");
    const sc = j.scan || {};
    const sev = (c) => c >= 9 ? "bad" : c >= 7 ? "warn" : c > 0 ? "" : "ok";
    view.innerHTML = `<div class="toolbar"><h2 class="grow">Inventario de servicios</h2>
        <a href="/api/inventory/export"><button>Exportar CSV</button></a>
        ${ADMIN ? `<button class="primary" id="iscan" ${sc.running ? "disabled" : ""}>${sc.running ? "Escaneando…" : "🔍 Escanear ahora"}</button>` : ""}</div>
      <p class="muted">Qué programas y versiones tiene abiertos cada equipo y si tienen vulnerabilidades conocidas (CVE).
        Se revisa solo cada ${j.every_days} días de madrugada.
        ${sc.running ? `<b>En curso: ${esc(sc.progress || "")}</b>` : sc.ts ? `Último escaneo ${ago(sc.ts)}: ${sc.hosts} equipos, ${sc.services} servicios, ${sc.vulnerable} con CVE altas.` : "Todavía no se escanea."}</p>
      ${j.devices.length ? j.devices.map((d) => `<div class="card" style="margin-bottom:var(--gap)">
        <h3>${esc(d.name)} <span class="muted small mono">${esc(d.ip || "")}</span>
          ${d.max_cvss ? `<span class="pill ${sev(d.max_cvss)}">CVSS ${d.max_cvss.toFixed(1)}</span>` : '<span class="pill ok">sin CVE conocidas</span>'}</h3>
        <table><tr><th>Puerto</th><th>Servicio</th><th>Versión</th><th>CVE</th></tr>
        ${d.services.map((s) => `<tr><td class="mono">${s.port}/${s.proto}</td><td>${esc(s.product || s.service || "?")}</td>
          <td class="mono small">${esc(s.version || "—")}</td>
          <td>${s.cves.length ? s.cves.slice(0, 5).map((c) => `<a href="https://nvd.nist.gov/vuln/detail/${esc(c.id)}" target="_blank" rel="noopener"
              class="pill ${sev(c.cvss)}">${esc(c.id)} · ${c.cvss}</a>`).join(" ") + (s.cves.length > 5 ? ` <span class="muted small">+${s.cves.length - 5}</span>` : "")
            : '<span class="muted">—</span>'}</td></tr>`).join("")}</table></div>`).join("")
        : '<div class="card"><p class="muted">Aún no hay datos. Pulsa «Escanear ahora» (tarda unos minutos).</p></div>'}
      <p class="muted small">Para un análisis más profundo (credenciales por defecto, configuraciones inseguras) usa Greenbone del laboratorio de seguridad.</p>`;
    $("#iscan")?.addEventListener("click", async () => {
      try { const r = await api("/api/inventory/scan", { method: "POST" }); toast(r.msg); route(); } catch (e) { toast(e.message); }
    });
    if (sc.running) setTimeout(() => location.hash === "#inventario" && route(), 15000);
  },

  async wifi() {
    const [j, d] = await Promise.all([api("/api/wifi"), ADMIN ? api("/api/devices") : Promise.resolve({ devices: [] })]);
    const bar = (s) => `<span class="pill ${s >= 60 ? "ok" : s >= 35 ? "warn" : "bad"}">${s}%</span>`;
    const ssids = j.networks.map((n) => n.ssid);
    view.innerHTML = `<div class="toolbar"><h2 class="grow">Mapa de Wi-Fi</h2><button id="wre">🔄 Volver a buscar</button></div>
      ${j.error ? `<div class="banner">${esc(j.error)}</div>` : ""}
      ${j.mine_configured ? "" : `<p class="muted small">Tus redes se adivinaron por los aparatos a los que está conectada la Pi.
        Si falta alguna, ponlas en Ajustes → YAML: <span class="mono">wifi: {ssids: ["Casa", "Casa-Arriba"]}</span>.</p>`}
      ${j.tips.length ? `<div class="card" style="margin-bottom:var(--gap)"><h3>💡 Sugerencias</h3>${j.tips.map((t) => `<div class="small" style="margin:4px 0">${esc(t)}</div>`).join("")}</div>` : ""}
      <div class="grid2">${j.networks.map((n) => `<div class="card"><h3>📶 ${esc(n.ssid)} <span class="muted small">${n.bands.join(" + ")}</span></h3>
        <table><tr><th>Punto de acceso</th><th>Banda</th><th>Canal</th><th>Señal</th><th>Seguridad</th></tr>
        ${n.aps.map((a) => `<tr><td class="mono small">${esc(a.bssid)}${a.connected ? " ⭐" : ""}<br><span class="muted">${esc(a.vendor || "")}</span></td>
          <td>${a.band}</td><td>${a.channel}</td><td>${bar(a.signal)}</td><td class="small">${esc(a.security || "abierta")}</td></tr>`).join("")}</table>
        <p class="small" style="margin-top:8px"><b>${n.devices.length}</b> equipo(s) asignados:
          ${n.devices.map((x) => `<span class="pill ${x.online ? "ok" : ""}">${esc(x.name)}</span>`).join(" ") || '<span class="muted">ninguno</span>'}</p></div>`).join("")
        || '<div class="card"><p class="muted">No encontré tus redes. ¿La Pi tiene Wi-Fi? (en Pi 4 sí, aunque esté por cable).</p></div>'}</div>
      <div class="card" style="margin-top:var(--gap)"><h3>Canales ocupados</h3><p class="muted small">${j.others} redes vecinas a la vista. Lo tuyo va en verde.</p>
        ${Object.entries(j.channels).map(([b, cs]) => `<p><b>${b}</b><br>${Object.entries(cs).map(([c, v]) =>
          `<span class="pill ${v.some((x) => x.mine) ? "ok" : v.length >= 4 ? "warn" : ""}" title="${esc(v.map((x) => x.ssid || "(oculta)").join(", "))}">canal ${c}: ${v.length}</span>`).join(" ")}</p>`).join("")}</div>
      ${ADMIN && ssids.length ? `<div class="card" style="margin-top:var(--gap)"><h3>¿A qué red se conecta cada equipo?</h3>
        <p class="muted small">El router no le cuenta a la Pi a qué red va cada equipo; dilo aquí una vez (${j.unassigned} en línea sin asignar).</p>
        <table>${d.devices.filter((x) => x.online).map((x) => `<tr><td>${esc(x.display_name)}</td><td><select data-wmac="${x.mac}">
          <option value="">—</option>${ssids.map((s) => `<option ${x.wifi === s ? "selected" : ""}>${esc(s)}</option>`).join("")}</select></td></tr>`).join("")}</table></div>` : ""}`;
    $("#wre").onclick = async () => { $("#wre").disabled = true; await api("/api/wifi?rescan=1").catch(() => {}); route(); };
    view.querySelectorAll("select[data-wmac]").forEach((s) => s.onchange = async () => {
      try { await api(`/api/devices/${s.dataset.wmac}/wifi`, { method: "POST", body: JSON.stringify({ ssid: s.value }) }); toast("Guardado"); }
      catch (e) { toast(e.message); }
    });
  },

  async parental() {
    const j = await api("/api/parental");
    const DAYS = [["mon", "Lun"], ["tue", "Mar"], ["wed", "Mié"], ["thu", "Jue"], ["fri", "Vie"], ["sat", "Sáb"], ["sun", "Dom"]];
    const POP = ["youtube", "tiktok", "instagram", "facebook", "whatsapp", "roblox", "fortnite", "discord", "twitch", "netflix", "snapchat", "steam"];
    const svcs = [...j.services].sort((a, b) => (POP.indexOf(b.id) >= 0) - (POP.indexOf(a.id) >= 0) || a.name.localeCompare(b.name));
    const sname = Object.fromEntries(j.services.map((s) => [s.id, s.name]));
    const dname = Object.fromEntries(j.devices.map((d) => [d.mac, d.name]));
    const edit = globalThis.HL_PAR || null;
    const r = edit || { name: "", macs: [], services: [], allow: {}, safe_search: 1, adult: 1, enabled: 1 };
    view.innerHTML = `<h2>Control parental</h2>
      ${j.error ? `<div class="banner">${esc(j.error)}</div>` : ""}
      <p class="muted">Bloquea apps y sitios por equipo, con horario. Funciona en los equipos que usan la Pi como DNS
        (el que da el router). Si el navegador tiene «DNS seguro» propio, apágalo en ese equipo.</p>
      <div class="grid2"><div class="card"><h3>${edit ? "Editar" : "Nueva"} regla</h3>
        <input id="pn" placeholder="Nombre, ej. Tablet de Sofi" value="${esc(r.name)}" style="width:100%">
        <p class="small"><b>Equipos</b></p><div style="max-height:160px;overflow:auto">${j.devices.map((d) =>
          `<label class="small" style="display:block"><input type="checkbox" class="pm" value="${d.mac}" ${r.macs.includes(d.mac) ? "checked" : ""}>
            ${esc(d.name)} <span class="muted mono">${esc(d.ip || "")}</span></label>`).join("")}</div>
        <p class="small"><b>Bloquear</b> <input id="pf" placeholder="buscar…" style="width:8em"></p>
        <div style="max-height:180px;overflow:auto" id="psv">${svcs.map((s) =>
          `<label class="small" style="display:inline-block;min-width:9em" data-n="${esc(s.name.toLowerCase())}">
            <input type="checkbox" class="ps" value="${esc(s.id)}" ${r.services.includes(s.id) ? "checked" : ""}> ${esc(s.name)}</label>`).join("")}</div>
        <p class="small"><b>Horas en que SÍ se permite</b> <span class="muted">(vacío = bloqueado todo el día)</span></p>
        <table>${DAYS.map(([k, t]) => `<tr><td>${t}</td><td><input type="time" data-d="${k}" data-i="0" value="${(r.allow[k] || [])[0] || ""}">
          a <input type="time" data-d="${k}" data-i="1" value="${(r.allow[k] || [])[1] || ""}"></td></tr>`).join("")}</table>
        <button id="pcopy" class="small">Copiar el lunes a toda la semana</button>
        <p><label><input type="checkbox" id="pss" ${r.safe_search ? "checked" : ""}> Búsqueda segura (Google, Bing, YouTube restringido)</label><br>
          <label><input type="checkbox" id="pad" ${r.adult ? "checked" : ""}> Bloquear sitios para adultos</label><br>
          <label><input type="checkbox" id="pen" ${r.enabled ? "checked" : ""}> Regla activa</label></p>
        <div class="toolbar"><button class="primary" id="psave">Guardar</button>${edit ? '<button id="pcancel">Cancelar</button>' : ""}</div></div>
      <div class="card"><h3>Reglas</h3>${j.rules.map((x) => `<div class="svc" style="margin-bottom:10px"><span>${x.enabled ? "🛡" : "⏸"}</span>
        <div style="min-width:0;flex:1"><b>${esc(x.name)}</b><div class="small muted">${x.macs.map((m) => esc(dname[m] || m)).join(", ")}</div>
        <div class="small">${x.services.map((s) => esc(sname[s] || s)).join(", ") || "sin apps bloqueadas"}
          ${Object.keys(x.allow).length ? " · permitido " + DAYS.filter(([k]) => x.allow[k]).map(([k, t]) => `${t} ${x.allow[k].join("–")}`).join(", ") : ""}
          ${x.adult ? " · sin adultos" : ""}${x.safe_search ? " · búsqueda segura" : ""}</div></div>
        <button data-pe="${x.id}">Editar</button><button class="danger" data-pd="${x.id}">Quitar</button></div>`).join("")
        || '<p class="muted">Sin reglas todavía.</p>'}</div></div>`;
    $("#pf").oninput = () => { const q = $("#pf").value.toLowerCase(); $("#psv").querySelectorAll("label").forEach((l) => l.hidden = q && !l.dataset.n.includes(q)); };
    $("#pcopy").onclick = () => {
      const a = $('input[data-d="mon"][data-i="0"]').value, b = $('input[data-d="mon"][data-i="1"]').value;
      view.querySelectorAll('input[data-i="0"]').forEach((i) => i.value = a);
      view.querySelectorAll('input[data-i="1"]').forEach((i) => i.value = b);
    };
    $("#pcancel")?.addEventListener("click", () => { globalThis.HL_PAR = null; route(); });
    $("#psave").onclick = async () => {
      const allow = {};
      for (const [k] of DAYS) {
        const a = $(`input[data-d="${k}"][data-i="0"]`).value, b = $(`input[data-d="${k}"][data-i="1"]`).value;
        if (a && b) allow[k] = [a, b];
      }
      const body = { id: edit?.id, name: $("#pn").value, allow,
        macs: [...view.querySelectorAll(".pm:checked")].map((i) => i.value),
        services: [...view.querySelectorAll(".ps:checked")].map((i) => i.value),
        safe_search: $("#pss").checked, adult: $("#pad").checked, enabled: $("#pen").checked };
      try { await api("/api/parental", { method: "POST", body: JSON.stringify(body) }); globalThis.HL_PAR = null; toast("Guardado en AdGuard"); route(); }
      catch (e) { toast(e.message); }
    };
    view.querySelectorAll("[data-pe]").forEach((b) => b.onclick = () => { globalThis.HL_PAR = j.rules.find((x) => x.id == b.dataset.pe); route(); });
    view.querySelectorAll("[data-pd]").forEach((b) => b.onclick = async () => {
      if (!confirm("¿Quitar esta regla?")) return;
      await api(`/api/parental/${b.dataset.pd}`, { method: "DELETE" }); route();
    });
  },

  async bots() {
    const j = await api("/api/bots");
    const tab = globalThis.HL_BOT || {};
    const SVC = { active: ["ok", "corriendo"], activating: ["warn", "arrancando"], deactivating: ["warn", "apagando"],
      inactive: ["", "apagado"], failed: ["bad", "falló"], none: ["", "sin servicio"] };
    const TYPE = { admin: "👑 admin", premium: "⭐ premium", subscribed: "💎 suscrito", unsubscribed: "gratis" };
    const fmtd = (s) => s ? s.slice(0, 16).replace("T", " ") : "—";
    if (!j.bots.length) {
      view.innerHTML = `<h2>Bots</h2><div class="card"><p>Aquí administras tus otros bots de Telegram: si están corriendo,
        reiniciarlos, sus logs y, en los de tipo <b>yt-telegrambot</b>, sus usuarios, premium y pagos con Stars.</p>
        <p>Regístralo una vez en la Pi:</p><pre class="mono">sudo ./hl bots add yt ~/yt-telegrambot</pre>
        <p class="muted small">Detecta el tipo y su servicio de systemd. El bot sigue corriendo igual que siempre.</p></div>`;
      return;
    }
    view.innerHTML = `<div class="toolbar"><h2 class="grow">Bots</h2>
        <span class="muted small">estado de systemd ${j.status_ts ? ago(j.status_ts) : "pidiéndose…"}</span>
        <button id="bst">🔄</button></div>
      ${j.bots.map((b) => {
        const [cls, label] = SVC[b.service.state] || ["", b.service.state || "¿?"];
        const me = b.me || {}, s = b.stats || {};
        return `<div class="card" style="margin-bottom:var(--gap)">
          <div class="toolbar"><h3 class="grow">🤖 ${esc(b.name)} ${me.username ? `<a href="https://t.me/${esc(me.username)}" target="_blank" rel="noopener">@${esc(me.username)}</a>` : ""}
            <span class="pill ${cls}">${label}</span>${me.pending >= 3 ? `<span class="pill warn">${me.pending} mensajes sin leer</span>` : ""}</h3>
            ${b.unit ? `<button data-bact="restart" data-b="${b.name}">↻ Reiniciar</button>
              ${b.service.state === "active" ? `<button data-bact="stop" data-b="${b.name}">⏹ Apagar</button>` : `<button class="primary" data-bact="start" data-b="${b.name}">▶ Encender</button>`}` : ""}
            <button data-blog="${b.name}">📜 Logs</button></div>
          <p class="muted small">${esc(b.kind)} · ${esc(b.unit || "sin servicio")} · <span class="mono">${esc(b.path)}</span>
            ${b.service.since ? ` · arriba ${ago(b.service.since)}` : ""}${b.service.restarts ? ` · ${b.service.restarts} reinicios` : ""}
            ${b.service.memory ? ` · ${Math.round(b.service.memory / 2 ** 20)} MB` : ""}</p>
          ${b.error ? `<div class="banner">${esc(b.error)}</div>` : ""}${me.error ? `<div class="banner">Telegram: ${esc(me.error)}</div>` : ""}
          ${b.kind === "yt-telegrambot" && !b.error ? (s.error ? `<div class="banner">${esc(s.error)}</div>` : `<div class="grid" style="margin:8px 0">
            ${[["Usuarios", s.total_users], ["Activos hoy", s.active_today], ["Premium", s.premium_flagged], ["Pagos", s.payment_count],
               ["Stars", `${s.stars_total} ⭐`], ["Canciones", s.songs != null ? `${s.songs}${s.songs_mb != null ? ` · ${s.songs_mb} MB` : ""}` : "—"],
               ["En cola", s.queue ? Object.entries(s.queue).map(([k, v]) => `${v} ${k}`).join(", ") || "0" : "—"],
               ["Gratis hoy", s.free_downloads_today]].map(([k, v]) => `<div class="stat"><span class="muted small">${k}</span><b>${v ?? "—"}</b></div>`).join("")}</div>
            <div class="toolbar"><button data-btab="users" data-b="${b.name}" class="${tab[b.name] === "users" ? "primary" : ""}">👥 Usuarios</button>
              <button data-btab="pay" data-b="${b.name}" class="${tab[b.name] === "pay" ? "primary" : ""}">⭐ Pagos</button>
              <button data-btab="songs" data-b="${b.name}" class="${tab[b.name] === "songs" ? "primary" : ""}">🎵 Canciones</button>
              <button data-btab="msg" data-b="${b.name}" class="${tab[b.name] === "msg" ? "primary" : ""}">✉ Mensaje</button></div>
            <div id="bt-${b.name}"></div>`) : ""}
          ${b.broadcast ? `<p class="small">✉ Aviso: ${b.broadcast.sent}/${b.broadcast.total} enviados${b.broadcast.failed ? `, ${b.broadcast.failed} fallaron` : ""}${b.broadcast.running ? "…" : "."}</p>` : ""}
          <pre id="bl-${b.name}" class="mono small" hidden style="max-height:360px;overflow:auto;white-space:pre-wrap"></pre></div>`;
      }).join("")}`;

    const act = async (name, a) => {
      try { await api(`/api/bots/${name}/do/${a}`, { method: "POST" }); toast("Pedido: se aplica en unos segundos"); }
      catch (e) { toast(e.message); }
      setTimeout(() => location.hash === "#bots" && route(), 4000);
    };
    $("#bst").onclick = () => act(j.bots[0].name, "status");
    view.querySelectorAll("[data-bact]").forEach((x) => x.onclick = () => {
      if (x.dataset.bact === "stop" && !confirm(`¿Apagar ${x.dataset.b}? Nadie podrá usarlo hasta que lo enciendas.`)) return;
      act(x.dataset.b, x.dataset.bact);
    });
    view.querySelectorAll("[data-blog]").forEach((x) => x.onclick = async () => {
      const n = x.dataset.blog, pre = $(`#bl-${n}`), before = (await api(`/api/bots/${n}/logs`)).ts;
      pre.hidden = false; pre.textContent = "Pidiendo los logs…";
      await api(`/api/bots/${n}/do/logs`, { method: "POST" }).catch((e) => { pre.textContent = e.message; });
      for (let i = 0; i < 15; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        const l = await api(`/api/bots/${n}/logs`);
        if (l.ts > before) { pre.textContent = l.text || "(vacío)"; pre.scrollTop = pre.scrollHeight; return; }
      }
      pre.textContent = "No llegaron. ¿Está instalado el servicio de aplicar? En la Pi:  sudo ./hl bots logs " + n;
    });
    view.querySelectorAll("[data-btab]").forEach((x) => x.onclick = () => {
      globalThis.HL_BOT = { ...tab, [x.dataset.b]: tab[x.dataset.b] === x.dataset.btab ? "" : x.dataset.btab }; route();
    });

    for (const b of j.bots) {
      const box = $(`#bt-${b.name}`), t = tab[b.name];
      if (!box || !t) continue;
      if (t === "users") {
        box.innerHTML = `<div class="toolbar"><input id="bq-${b.name}" placeholder="Buscar ID o @usuario" value="${esc(globalThis.HL_BOTQ || "")}"></div><div id="bu-${b.name}">Cargando…</div>`;
        const load = async () => {
          const q = $(`#bq-${b.name}`).value; globalThis.HL_BOTQ = q;
          const r = await api(`/api/bots/${b.name}/users?q=${encodeURIComponent(q)}`).catch((e) => ({ error: e.message, users: [] }));
          $(`#bu-${b.name}`).innerHTML = r.error ? `<div class="banner">${esc(r.error)}</div>` : `<table><tr><th>Usuario</th><th>Tipo</th><th>Vence</th><th>Hoy</th><th>Última vez</th><th>Pagos</th><th></th></tr>
            ${r.users.map((u) => `<tr><td>${u.username ? "@" + esc(u.username) : "—"}<br><span class="mono small muted">${u.telegram_id}</span></td>
              <td><span class="pill ${u.active && u.type_user !== "unsubscribed" ? "ok" : ""}">${TYPE[u.type_user] || esc(u.type_user)}</span>${u.operator ? ' <span class="muted small">(ADMIN_IDS)</span>' : ""}</td>
              <td class="small">${u.premium_until ? fmtd(u.premium_until) : u.type_user !== "unsubscribed" ? "nunca" : "—"}</td>
              <td>${u.songs_by_day}</td><td class="small">${fmtd(u.last_request_time)}</td>
              <td class="small">${u.payments ? `${u.payments} · ${u.stars} ⭐` : "—"}</td>
              <td><select data-uop="${u.telegram_id}" data-b="${b.name}"><option value="">Acción…</option>
                <option value="grant:premium:30">⭐ Premium 30 días</option><option value="grant:premium:365">⭐ Premium 1 año</option>
                <option value="grant:premium:ask">⭐ Premium N días…</option><option value="grant:premium:0">⭐ Premium sin vencer</option>
                <option value="grant:admin:0">👑 Hacer admin</option><option value="revoke">Regresar a gratis</option>
                <option value="reset">Cuota de hoy en 0</option><option value="msg">✉ Mandarle mensaje</option>
                <option value="delete">🗑 Borrar usuario</option></select></td></tr>`).join("") || '<tr><td colspan="7" class="muted">Nadie</td></tr>'}</table>`;
          box.querySelectorAll("[data-uop]").forEach((sel) => sel.onchange = async () => {
            const [op, type, d] = sel.value.split(":"), uid = sel.dataset.uop; sel.value = "";
            if (!op) return;
            try {
              if (op === "msg") {
                const text = prompt("Mensaje para " + uid); if (!text) return;
                toast((await api(`/api/bots/${b.name}/message`, { method: "POST", body: JSON.stringify({ to: uid, text }) })).msg); return;
              }
              let days = d === "ask" ? prompt("¿Cuántos días? (se suman a lo que le quede)") : d;
              if (days === null) return;
              if (op === "delete" && !confirm(`¿Borrar a ${uid} del bot? Pierde su premium e historial.`)) return;
              const r = await api(`/api/bots/${b.name}/users/${uid}`, { method: "POST", body: JSON.stringify({ op, type, days: Number(days) || 0 }) });
              toast(r.msg); load();
            } catch (e) { toast(e.message); }
          });
        };
        let tmr; $(`#bq-${b.name}`).oninput = () => { clearTimeout(tmr); tmr = setTimeout(load, 300); };
        load();
      } else if (t === "pay") {
        const r = await api(`/api/bots/${b.name}/payments`).catch((e) => ({ error: e.message, payments: [] }));
        box.innerHTML = r.error ? `<div class="banner">${esc(r.error)}</div>` : `<table><tr><th>Fecha</th><th>Usuario</th><th>Plan</th><th>Stars</th><th>Días</th><th></th></tr>
          ${r.payments.map((p) => `<tr><td class="small">${fmtd(p.created_at)}</td><td>${p.username ? "@" + esc(p.username) : ""} <span class="mono small muted">${p.telegram_id}</span></td>
            <td>${esc(p.plan_code)}</td><td>${p.stars} ⭐</td><td>${p.days}</td>
            <td>${p.refunded ? '<span class="pill">reembolsado</span>' : `<button class="danger" data-refund="${esc(p.charge_id)}" data-st="${p.stars}" data-u="${p.telegram_id}">Reembolsar</button>`}</td></tr>`).join("")
            || '<tr><td colspan="6" class="muted">Sin pagos todavía</td></tr>'}</table>`;
        box.querySelectorAll("[data-refund]").forEach((x) => x.onclick = async () => {
          if (!confirm(`¿Regresar ${x.dataset.st} ⭐ a ${x.dataset.u}? Telegram se las devuelve y pierde su premium. No se puede deshacer.`)) return;
          try { toast((await api(`/api/bots/${b.name}/refund`, { method: "POST", body: JSON.stringify({ charge_id: x.dataset.refund }) })).msg); route(); }
          catch (e) { toast(e.message); }
        });
      } else if (t === "songs") {
        const mmss = (n) => n ? `${Math.floor(n / 60)}:${String(n % 60).padStart(2, "0")}` : "—";
        box.innerHTML = `<div class="toolbar"><input id="sq-${b.name}" placeholder="Buscar título, canal o ID de YouTube" value="${esc(globalThis.HL_SONGQ || "")}" class="grow">
            <select id="ss-${b.name}"><option value="recent">Más recientes</option><option value="name">Título</option>
              <option value="channel">Canal</option><option value="duration">Más largas</option></select>
            <button class="danger" id="sd-${b.name}" disabled>🗑 Borrar seleccionadas</button></div>
          <p class="muted small">Borrar quita la canción de <span class="mono">idSongs.db</span>, su audio de <span class="mono">Songs/</span> y su miniatura.
            Si alguien la vuelve a pedir, el bot la baja de nuevo.</p><div id="sl-${b.name}">Cargando…</div>`;
        const sel = new Set(), btn = $(`#sd-${b.name}`), sortSel = $(`#ss-${b.name}`);
        sortSel.value = globalThis.HL_SONGS || "recent";
        const upd = () => { btn.disabled = !sel.size; btn.textContent = sel.size ? `🗑 Borrar ${sel.size}` : "🗑 Borrar seleccionadas"; };
        const del = async (ids, orphans, what) => {
          if (!confirm(`¿Borrar ${what}? Se quita de la base del bot y del disco; no se puede deshacer.`)) return;
          try {
            const r = await api(`/api/bots/${b.name}/songs/delete`, { method: "POST", body: JSON.stringify({ ids, orphans }) });
            toast(`Borradas ${r.removed} · ${r.freed_mb} MB liberados${r.skipped.length ? ` · ${r.skipped.length} se están enviando ahora, no se tocaron` : ""}`);
            sel.clear(); upd(); load();
          } catch (e) { toast(e.message); }
        };
        const load = async () => {
          const q = $(`#sq-${b.name}`).value.trim(); globalThis.HL_SONGQ = q; globalThis.HL_SONGS = sortSel.value;
          const r = await api(`/api/bots/${b.name}/songs?q=${encodeURIComponent(q)}&sort=${sortSel.value}`).catch((e) => ({ error: e.message }));
          const box2 = $(`#sl-${b.name}`);
          if (r.error) { box2.innerHTML = `<div class="banner">${esc(r.error)}</div>`; return; }
          box2.innerHTML = `<p class="small">${r.total} canción(es)${r.total > r.songs.length ? `, se muestran ${r.songs.length}` : ""}
              · ${r.songs.reduce((a, x) => a + x.mb, 0).toFixed(0)} MB en esta lista</p>
            <table><tr><th><input type="checkbox" id="sa-${b.name}" title="Seleccionar todas"></th><th>Título</th><th>Canal</th><th>Duración</th><th>Archivo</th><th></th></tr>
            ${r.songs.map((x) => `<tr><td><input type="checkbox" data-sid="${x.id}" ${x.busy ? "disabled" : ""} ${sel.has(x.id) ? "checked" : ""}></td>
              <td>${esc(x.name || "—")}${x.uri ? `<br><a class="mono small muted" href="https://youtu.be/${encodeURIComponent(x.uri)}" target="_blank" rel="noopener">${esc(x.uri)}</a>` : ""}</td>
              <td class="small">${esc(x.channel || "")}</td><td class="mono small">${mmss(x.duration)}</td>
              <td class="small">${x.files.length ? x.files.map((f) => `${esc(f.name.split(".").pop())} · ${f.mb} MB`).join("<br>") : '<span class="muted">solo en la base</span>'}</td>
              <td>${x.busy ? '<span class="pill warn">enviándose</span>' : `<button class="danger" data-sdel="${x.id}" title="Borrar">🗑</button>`}</td></tr>`).join("")
              || '<tr><td colspan="6" class="muted">Nada con ese filtro</td></tr>'}</table>
            ${r.orphans.length ? `<div class="card" style="margin-top:var(--gap)"><h3>Archivos sin registro</h3>
              <p class="muted small">${r.orphans.length} audio(s) en <span class="mono">Songs/</span> que la base no conoce (${r.orphans_mb} MB). El bot no los usa.</p>
              <div class="small mono" style="max-height:140px;overflow:auto">${r.orphans.map((o) => `${esc(o.name)} · ${o.mb} MB`).join("<br>")}</div>
              <button class="danger" id="so-${b.name}">🗑 Borrar estos archivos</button></div>` : ""}`;
          box2.querySelectorAll("[data-sid]").forEach((c) => c.onchange = () => { c.checked ? sel.add(+c.dataset.sid) : sel.delete(+c.dataset.sid); upd(); });
          $(`#sa-${b.name}`).onchange = (e) => box2.querySelectorAll("[data-sid]:not(:disabled)").forEach((c) => { c.checked = e.target.checked; c.onchange(); });
          box2.querySelectorAll("[data-sdel]").forEach((x) => x.onclick = () => del([+x.dataset.sdel], [], "esta canción"));
          $(`#so-${b.name}`)?.addEventListener("click", () => del([], r.orphans.map((o) => o.name), `${r.orphans.length} archivo(s) sin registro`));
        };
        btn.onclick = () => del([...sel], [], `${sel.size} canción(es)`);
        let tm; $(`#sq-${b.name}`).oninput = () => { clearTimeout(tm); tm = setTimeout(load, 300); };
        sortSel.onchange = load;
        load();
      } else if (t === "msg") {
        box.innerHTML = `<p class="muted small">Un aviso a todos los usuarios del bot (mantenimiento, novedades). Llega como mensaje del propio bot.</p>
          <textarea id="bm-${b.name}" rows="4" style="width:100%" maxlength="4000" placeholder="Ej.: El bot estará en mantenimiento hoy de 10 a 11 pm 🙏"></textarea>
          <div class="toolbar"><button class="primary" id="bms-${b.name}">Enviar a todos (${b.stats.total_users ?? "?"})</button></div>`;
        $(`#bms-${b.name}`).onclick = async () => {
          const text = $(`#bm-${b.name}`).value.trim(); if (!text) return;
          if (!confirm(`¿Mandar este mensaje a los ${b.stats.total_users} usuarios de ${b.name}?`)) return;
          try { toast((await api(`/api/bots/${b.name}/message`, { method: "POST", body: JSON.stringify({ to: "all", text }) })).msg); }
          catch (e) { toast(e.message); }
          setTimeout(() => location.hash === "#bots" && route(), 3000);
        };
      }
    }
  },

  async acl() {
    const [j, d] = await Promise.all([api("/api/acl"), api("/api/devices")]);
    view.innerHTML = `<h2>Control de acceso (ACL)</h2>
      ${j.enforced ? "" : `<div class="banner">Sin modo puente las reglas no cortan el tráfico: solo te avisan cuando el dispositivo se conecta.
        Para bloquear de verdad pon la Pi entre el módem y tus AP (docs/modo-puente.md).</div>`}
      <div class="card"><h3>Nueva regla</h3><div class="toolbar">
        <select id="am"><option value="">Elige un dispositivo…</option>${d.devices.map((x) =>
          `<option value="${x.mac}">${esc(x.display_name)} · ${x.mac}</option>`).join("")}</select>
        <span class="muted">o MAC</span><input id="amac" placeholder="aa:bb:cc:dd:ee:ff" class="mono">
        <span class="muted">horario (opcional)</span><input id="as" type="time"><span>a</span><input id="ae" type="time">
        <input id="an" placeholder="nota (ej. sin internet de noche)">
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
    const f = (globalThis.HL_EVF ??= { level: "", kind: "", q: "", fp: "", days: "" });
    const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v)).toString();
    const [j, sr] = await Promise.all([api("/api/events?limit=500&" + qs),
      ADMIN ? api("/api/suppress") : Promise.resolve({ rules: [] })]);
    const kinds = Object.keys(j.kinds || {}).sort();
    const opt = (v, t, cur) => `<option value="${esc(v)}" ${v === cur ? "selected" : ""}>${esc(t)}</option>`;
    view.innerHTML = `<div class="toolbar"><h2 class="grow">Bitácora de eventos</h2>
        <a href="/api/events/export?format=csv&${qs}"><button>Exportar CSV</button></a>
        <a href="/api/events/export?format=json&${qs}"><button>JSON</button></a>
        <a href="/api/export/all"><button title="Bitácora, dispositivos, conexiones, ACL y falsos positivos">Exportar todo (.zip)</button></a>
        ${ADMIN ? '<button id="ev-clear">Limpiar…</button>' : ""}</div>
      <div class="card"><div class="toolbar" style="gap:.5rem;flex-wrap:wrap">
        <select id="f-level">${opt("", "Todos los niveles", f.level)}${opt("critical", "Críticos", f.level)}${opt("warning", "Avisos", f.level)}${opt("info", "Información", f.level)}</select>
        <select id="f-kind">${opt("", "Todos los tipos", f.kind)}${kinds.map((k) => opt(k, `${EV_ICON[k] || "•"} ${k} (${j.kinds[k]})`, f.kind)).join("")}</select>
        <select id="f-days">${opt("", "Siempre", f.days)}${opt("1", "Último día", f.days)}${opt("7", "Última semana", f.days)}${opt("30", "Último mes", f.days)}</select>
        <select id="f-fp">${opt("", "Con y sin falsos positivos", f.fp)}${opt("0", "Sin falsos positivos", f.fp)}${opt("1", "Solo falsos positivos", f.fp)}</select>
        <input id="f-q" placeholder="Buscar (texto, IP, MAC)" value="${esc(f.q)}" style="flex:1;min-width:10rem">
        <span class="muted small">${j.events.length} eventos</span></div>
        ${evList(j.events, ADMIN)}</div>
      ${ADMIN && sr.rules.length ? `<div class="card"><h3>Reglas de falso positivo <span class="muted small">(ya no avisan; siguen en la bitácora)</span></h3>
        ${sr.rules.map((r) => `<div class="svc"><span>${EV_ICON[r.kind] || "•"}</span><div style="min-width:0"><b>${esc(r.kind)}</b>
          <div class="muted small ellipsis">${esc(r.mac || r.title || "todos los de este tipo")}${r.note ? " · " + esc(r.note) : ""} · ${r.hits} veces silenciado</div></div>
          <button class="small" data-unrule="${r.id}">Quitar regla</button></div>`).join("")}</div>` : ""}`;
    const set = (k, v) => { f[k] = v; views.eventos(); };
    $("#f-level").onchange = (e) => set("level", e.target.value);
    $("#f-kind").onchange = (e) => set("kind", e.target.value);
    $("#f-days").onchange = (e) => set("days", e.target.value);
    $("#f-fp").onchange = (e) => set("fp", e.target.value);
    $("#f-q").onchange = (e) => set("q", e.target.value.trim());
    view.querySelectorAll("[data-fp]").forEach((b) => b.onclick = async () => {
      const note = prompt("¿Por qué es falso positivo? (opcional, p. ej. «es la tele de la sala»)", "");
      if (note === null) return;
      const sup = confirm("¿Ya no avisar de esto mismo en el futuro?\n\nAceptar = no volver a avisar (queda en la bitácora)\nCancelar = solo marcar este");
      try { await api(`/api/events/${b.dataset.fp}/fp`, { method: "POST", body: JSON.stringify({ note, suppress: sup }) });
        toast(sup ? "Marcado; ya no te avisará de esto" : "Marcado como falso positivo"); views.eventos(); } catch (e) { toast(e.message); }
    });
    view.querySelectorAll("[data-unfp]").forEach((b) => b.onclick = async () => {
      await api(`/api/events/${b.dataset.unfp}/fp`, { method: "DELETE" }); views.eventos();
    });
    view.querySelectorAll("[data-unrule]").forEach((b) => b.onclick = async () => {
      if (!confirm("¿Volver a avisar de esto?")) return;
      await api(`/api/suppress/${b.dataset.unrule}`, { method: "DELETE" }); toast("Regla quitada"); views.eventos();
    });
    $("#ev-clear")?.addEventListener("click", async () => {
      const a = prompt("¿Qué borro de la bitácora?\n\n  1 = más viejos de 30 días\n  2 = más viejos de 7 días\n  3 = solo los falsos positivos\n  4 = TODO\n\n(Tip: antes usa «Exportar todo» para guardar una copia)", "1");
      const opts = { 1: { older_than_days: 30 }, 2: { older_than_days: 7 }, 3: { only_fp: true }, 4: {} }[a?.trim()];
      if (!opts) return;
      if (a.trim() === "4" && !confirm("¿Seguro? Se borra toda la bitácora.")) return;
      try { const r = await api("/api/events/clear", { method: "POST", body: JSON.stringify(opts) });
        toast(`Listo: ${r.deleted} eventos borrados`); views.eventos(); } catch (e) { toast(e.message); }
    });
  },
};

const EV_ICON = { new_device: "🆕", arp_spoof: "🕵", ports_changed: "🚪", ids: "🚨", blocked_seen: "⛔",
  quarantine: "🧪", acl: "🔒", honeypot: "🍯", dns_danger: "☣", roam_failed: "📶", login_failed: "🔑",
  login_new_ip: "👤", camera_found: "📷", wol: "⏻", log_cleared: "🧹", vulnerable: "🧬", crowdsec: "🛑", bots: "🤖", bot_down: "🤖" };
function evList(evs, actions = false) {
  if (!evs.length) return '<p class="muted">Sin eventos. Todo tranquilo.</p>';
  return evs.map((e) => `<div class="ev${e.fp ? " fp" : ""}"><span>${EV_ICON[e.kind] || "•"}</span>
    <span class="t">${fmtTime(e.ts)}</span><div style="flex:1;min-width:0"><b class="lvl-${e.level}">${esc(e.title)}</b>
    <span class="muted small">${esc(e.detail)}</span>
    ${e.fp ? `<span class="small" style="color:var(--muted)"> · falso positivo${e.fp_note ? ": " + esc(e.fp_note) : ""}</span>` : ""}</div>
    ${actions ? (e.fp ? `<button class="small" data-unfp="${e.id}" title="Quitar la marca">↺</button>`
                      : `<button class="small" data-fp="${e.id}" title="Marcar como falso positivo">Falso positivo</button>`) : ""}</div>`).join("");
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
let ADMIN = true, LOGS = true, ME = null;
const TITLES = { inicio: "Inicio", resumen: "Resumen", dispositivos: "Dispositivos", trafico: "Tráfico",
  conexiones: "Conexiones", ap: "Por AP", mapa: "Mapa", camaras: "Cámaras", servicios: "Servicios",
  accesos: "Accesos", eventos: "Eventos", acl: "ACL", vpn: "VPN", sistema: "Sistema", cuentas: "Cuentas",
  ajustes: "Ajustes", cuenta: "Mi cuenta", respaldos: "Mis respaldos", inventario: "Inventario", wifi: "Wi-Fi",
  parental: "Control parental", bots: "Bots" };
const ADMIN_ONLY = ["acl", "vpn", "sistema", "cuentas", "ajustes", "parental", "bots"];
const LOGS_ONLY = ["resumen", "dispositivos", "trafico", "conexiones", "ap", "mapa", "eventos", "inventario", "wifi"];
const setNav = (open) => {
  document.body.classList.toggle("nav-open", open);
  $("#scrim").hidden = !open;
  $("#menu").setAttribute("aria-expanded", open);
};
$("#menu").onclick = () => setNav(!document.body.classList.contains("nav-open"));
$("#scrim").onclick = () => setNav(false);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") setNav(false); });

async function route() {
  clearInterval(timer);
  setNav(false);
  let name = (location.hash || "#inicio").slice(1).split("?")[0];
  if (!views[name]) name = "inicio";
  if ((!ADMIN && ADMIN_ONLY.includes(name)) || (!LOGS && LOGS_ONLY.includes(name))) name = "inicio";
  document.querySelectorAll("#tabs a").forEach((a) => a.classList.toggle("on", a.getAttribute("href") === "#" + name));
  $("#title").textContent = TITLES[name] || "MyHomeLab";
  document.title = `${TITLES[name] || "Inicio"} · MyHomeLab`;
  try {
    await views[name]();
    // Toda tabla ancha se puede deslizar de lado en vez de salirse de la tarjeta
    view.querySelectorAll("table:not(.cards)").forEach((t) => {
      if (!t.parentElement.classList.contains("table-wrap")) {
        const w = document.createElement("div"); w.className = "table-wrap"; t.before(w); w.append(t);
      }
    });
  } catch (e) {
    view.innerHTML = `<div class="banner">Error: ${esc(e.message)}</div>`;
  }
  if (["resumen", "eventos", "servicios"].includes(name)) timer = setInterval(() => (views[name])(), 60000);
}
window.addEventListener("hashchange", route);
api("/api/me", { noRedirect: true }).then((m) => {
  if (m.user) {
    ME = m; ADMIN = m.role === "admin"; LOGS = !!m.logs;
    $("#me").textContent = m.user; $("#role").textContent = m.role_name || m.role;
    $("#avatar").textContent = m.user.slice(0, 1).toUpperCase();
    document.querySelectorAll("[data-admin]").forEach((el) => { el.hidden = !ADMIN; });
    document.querySelectorAll("[data-logs]").forEach((el) => { el.hidden = !LOGS; });
    setUnread(m.unread);
  }
}).catch(() => {}).finally(route);
function setUnread(n) { const b = $("#unread"); if (b) { b.hidden = !n; b.textContent = n || ""; } }
api("/api/summary", { noRedirect: true }).then((s) => {
  const t = s.bridge_mode ? `puente · ${s.interface}` : `pasivo · ${s.interface}`;
  ["#mode", "#mode-m"].forEach((id) => { $(id).textContent = t; $(id).className = "pill " + (s.bridge_mode ? "ok" : ""); });
}).catch(() => {});
