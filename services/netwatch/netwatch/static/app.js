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
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${g}${lines}</svg><div class="legend">${legend}</div>`;
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
              ${d.known ? "" : '<span class="new">NUEVO</span>'}<div class="muted small">${esc(d.hostname || "")}</div></td>
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
        <input id="flt" placeholder="Buscar nombre, IP, MAC, fabricante…" style="width:280px">
        <button id="allknown">✔ Marcar todos como revisados</button></div>
      <div class="card" style="overflow:auto"><table><thead><tr>
        <th data-k="online"></th><th data-k="display_name">Nombre</th><th data-k="display_type">Tipo</th><th data-k="ip">IP / red</th>
        <th data-k="mac">MAC / fabricante</th><th data-k="ap">AP</th><th>Puertos</th><th data-k="bytes_down">Tráfico</th><th data-k="last_seen">Visto</th><th></th>
      </tr></thead><tbody id="tb"></tbody></table></div>
      <p class="muted small">El AP no se puede saber por ARP: asígnalo aquí y aparecerá en el mapa y en «Por AP». El tráfico por dispositivo solo existe en modo puente.</p>`;
    draw();
    $("#flt").oninput = (e) => { filter = e.target.value.toLowerCase(); draw(); };
    view.querySelectorAll("th[data-k]").forEach((th) => th.onclick = () => {
      asc = sortKey === th.dataset.k ? !asc : true; sortKey = th.dataset.k; draw();
    });
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
  quarantine: "🧪", acl: "🔒" };
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
