/* Cine en casa — portal para Jellyfin (sin dependencias; hls.js solo si el navegador lo necesita) */
"use strict";

const CFG = window.CINE || {};
const BASE = (CFG.jellyfin || "/jellyfin").replace(/\/$/, "");
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const app = $("#app");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const TICKS = 1e7;

/* ------------------------------------------------------------ almacenamiento */
const store = {
  get(k, d = null) { try { const v = localStorage.getItem("cine." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("cine." + k, JSON.stringify(v)); } catch { /* modo privado */ } },
  del(k) { try { localStorage.removeItem("cine." + k); } catch { /* nada */ } },
};
let deviceId = store.get("device");
if (!deviceId) { deviceId = "cine-" + Math.random().toString(36).slice(2) + Date.now().toString(36); store.set("device", deviceId); }
let S = store.get("session");          // {userId, token, name, imageTag}
const TV = new URLSearchParams(location.search).has("tele") || store.get("tv") || matchMedia("(pointer: none)").matches;
if (new URLSearchParams(location.search).has("tele")) store.set("tv", true);
document.body.classList.toggle("tv", !!TV);
document.title = $("#brandname").textContent = CFG.title || "Cine en casa";

const deviceName = /Android/i.test(navigator.userAgent) ? "Celular" : /iPhone|iPad/i.test(navigator.userAgent) ? "iPhone"
  : TV ? "Tele" : "Navegador";
function authHeader() {
  return `MediaBrowser Client="Cine en casa", Device="${deviceName}", DeviceId="${deviceId}", Version="1.0.0"` +
    (S?.token ? `, Token="${S.token}"` : "");
}
async function api(path, { method = "GET", body, quiet } = {}) {
  const r = await fetch(BASE + path, {
    method, headers: { "Authorization": authHeader(), ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (r.status === 401 && !quiet) { logout("Tu sesión terminó: elige tu perfil otra vez."); throw new Error("401"); }
  if (!r.ok) throw new Error(`Jellyfin respondió ${r.status}`);
  const t = await r.text();
  return t ? JSON.parse(t) : null;
}
const UID = () => `userId=${S.userId}`;      // Jellyfin 10.9+: el usuario va como parámetro, no en la ruta
const FIELDS = "Overview,Genres,ProductionYear,OfficialRating,CommunityRating,RunTimeTicks,ChildCount,PrimaryImageAspectRatio,ParentId,SeriesInfo";

function img(id, type = "Primary", { h, w, tag, idx = 0 } = {}) {
  if (!id) return "";
  const q = new URLSearchParams({ quality: "90" });
  if (h) q.set("fillHeight", h); if (w) q.set("fillWidth", w); if (tag) q.set("tag", tag);
  return `${BASE}/Items/${id}/Images/${type}${type === "Backdrop" ? "/" + idx : ""}?${q}`;
}
const posterOf = (it, h = 480) => it.ImageTags?.Primary ? img(it.Id, "Primary", { h, tag: it.ImageTags.Primary })
  : it.SeriesPrimaryImageTag ? img(it.SeriesId, "Primary", { h, tag: it.SeriesPrimaryImageTag }) : "";
function wideOf(it, w = 640) {
  if (it.Type === "Episode" && it.ImageTags?.Primary) return img(it.Id, "Primary", { w, tag: it.ImageTags.Primary });
  if (it.ImageTags?.Thumb) return img(it.Id, "Thumb", { w, tag: it.ImageTags.Thumb });
  if (it.BackdropImageTags?.length) return img(it.Id, "Backdrop", { w, tag: it.BackdropImageTags[0] });
  if (it.ParentThumbItemId) return img(it.ParentThumbItemId, "Thumb", { w, tag: it.ParentThumbImageTag });
  if (it.ParentBackdropItemId) return img(it.ParentBackdropItemId, "Backdrop", { w, tag: it.ParentBackdropImageTags?.[0] });
  return posterOf(it, 360);
}
const backdropOf = (it) => it.BackdropImageTags?.length ? img(it.Id, "Backdrop", { w: 1920, tag: it.BackdropImageTags[0] })
  : it.ParentBackdropItemId ? img(it.ParentBackdropItemId, "Backdrop", { w: 1920, tag: it.ParentBackdropImageTags?.[0] }) : "";
const logoOf = (it) => it.ImageTags?.Logo ? img(it.Id, "Logo", { w: 900, tag: it.ImageTags.Logo })
  : it.ParentLogoItemId ? img(it.ParentLogoItemId, "Logo", { w: 900, tag: it.ParentLogoImageTag }) : "";

/* --------------------------------------------------------------- utilidades */
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove("show"), 2600); }
const runtime = (ticks) => {
  if (!ticks) return "";
  const m = Math.round(ticks / TICKS / 60);
  return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60 ? (m % 60) + " min" : ""}`.trim() : `${m} min`;
};
const clock = (s) => {
  s = Math.max(0, Math.floor(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${x}` : `${m}:${x}`;
};
const pct = (it) => it.UserData?.PlayedPercentage || (it.UserData?.PlaybackPositionTicks && it.RunTimeTicks
  ? it.UserData.PlaybackPositionTicks / it.RunTimeTicks * 100 : 0);
const epCode = (it) => it.ParentIndexNumber != null && it.IndexNumber != null ? `T${it.ParentIndexNumber} E${it.IndexNumber}` : "";
const COLORS = ["#6E1E2B", "#1F4E5F", "#4A2A5E", "#8A5A12", "#24493A", "#5E2440"];   // terciopelo, petróleo, ciruela, ocre, botella, vino
const colorFor = (s) => COLORS[[...String(s)].reduce((a, c) => a + c.charCodeAt(0), 0) % COLORS.length];
const ICON = {
  play: '<svg viewBox="0 0 24 24"><path d="M7 4.5v15l12.5-7.5z"/></svg>',
  pause: '<svg viewBox="0 0 24 24" class="fill"><rect x="6" y="4.5" width="4" height="15" rx="1"/><rect x="14" y="4.5" width="4" height="15" rx="1"/></svg>',
  info: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>',
  plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
  check: '<svg viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>',
  back: '<svg viewBox="0 0 24 24"><path d="M15 5l-7 7 7 7"/></svg>',
  rew: '<svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/><text x="12" y="15.5" font-size="7" text-anchor="middle" stroke="none" fill="currentColor" font-family="sans-serif" font-weight="700">10</text></svg>',
  fwd: '<svg viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 4v5h-5"/><text x="12" y="15.5" font-size="7" text-anchor="middle" stroke="none" fill="currentColor" font-family="sans-serif" font-weight="700">10</text></svg>',
  cc: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="3"/><path d="M10 10.5a2 2 0 1 0 0 3M16 10.5a2 2 0 1 0 0 3"/></svg>',
  audio: '<svg viewBox="0 0 24 24"><path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2"/></svg>',
  next: '<svg viewBox="0 0 24 24"><path d="M5 5v14l10-7zM19 5v14"/></svg>',
  full: '<svg viewBox="0 0 24 24"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>',
  home: '<svg viewBox="0 0 24 24"><path d="M3 11 12 4l9 7v9H3z"/></svg>',
  film: '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/></svg>',
  tv: '<svg viewBox="0 0 24 24"><rect x="3" y="6" width="18" height="12" rx="2"/><path d="m8 3 4 3 4-3"/></svg>',
  search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></svg>',
  user: '<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/></svg>',
};

/* ------------------------------------------------------------- tarjetas */
function card(it, { wide = false, link } = {}) {
  const href = link || (it.Type === "Episode" && wide ? `#/ver/${it.Id}` : `#/item/${it.Type === "Episode" && !wide ? it.SeriesId : it.Id}`);
  const src = wide ? wideOf(it) : posterOf(it);
  const name = it.Type === "Episode" ? (wide ? it.SeriesName : it.SeriesName || it.Name) : it.Name;
  const sub = it.Type === "Episode" ? `${epCode(it)} ${it.Name}`.trim()
    : it.Type === "Series" ? (it.ProductionYear ? `${it.ProductionYear}${it.Status === "Continuing" ? " en emisión" : ""}` : "")
    : it.ProductionYear || "";
  const p = pct(it);
  return `<a class="card${wide ? " wide" : ""}" href="${href}" data-id="${it.Id}">
    <div class="art">${src ? `<img loading="lazy" src="${esc(src)}" alt="">` : `<div class="ph">${esc(name)}</div>`}
      ${it.UserData?.Played && !wide ? '<span class="watched" title="Vista">✓</span>' : ""}
      ${p > 1 && p < 99 ? `<div class="progress"><i style="width:${p.toFixed(1)}%"></i></div>` : ""}</div>
    <div class="name">${esc(name)}</div>${sub ? `<div class="sub">${esc(sub)}</div>` : ""}</a>`;
}
const row = (title, items, opts = {}) => items?.length ? `<section class="row"><h2>${opts.href ? `<a href="${opts.href}" tabindex="-1">${esc(title)}</a>` : esc(title)}</h2>
  <div class="track">${items.map((i) => card(i, opts)).join("")}</div></section>` : "";
function facts(it) {
  const f = [];
  if (it.CommunityRating) f.push(`<span class="rating">★ ${it.CommunityRating.toFixed(1)}</span>`);
  if (it.ProductionYear) f.push(`<span>${it.ProductionYear}</span>`);
  if (it.Type === "Series" && it.ChildCount) f.push(`<span>${it.ChildCount} temporada${it.ChildCount > 1 ? "s" : ""}</span>`);
  else if (it.RunTimeTicks) f.push(`<span>${runtime(it.RunTimeTicks)}</span>`);
  if (it.OfficialRating) f.push(`<span class="badge">${esc(it.OfficialRating)}</span>`);
  if (it.Genres?.length) f.push(`<span>${esc(it.Genres.slice(0, 3).join(", "))}</span>`);
  return `<div class="facts">${f.join("")}</div>`;
}

/* -------------------------------------------------------------- barra/nav */
let VIEWS = [];
function chrome(on, active) {
  $("#bar").hidden = $("#tabbar").hidden = !on;
  if (!on) return;
  const movies = VIEWS.find((v) => v.CollectionType === "movies"), shows = VIEWS.find((v) => v.CollectionType === "tvshows");
  const links = [["inicio", "#/inicio", "Inicio", ICON.home], movies && ["peliculas", `#/biblioteca/${movies.Id}`, "Películas", ICON.film],
    shows && ["series", `#/biblioteca/${shows.Id}`, "Series", ICON.tv], ["milista", "#/milista", "Mi lista", ICON.plus]].filter(Boolean);
  $("#nav").innerHTML = links.map(([k, h, t]) => `<a href="${h}" class="${k === active ? "on" : ""}">${t}</a>`).join("");
  $("#tabbar").innerHTML = [...links.slice(0, 3), ["buscar", "#/buscar", "Buscar", ICON.search], ["perfil", "#/perfiles", "Perfil", ICON.user]]
    .map(([k, h, t, i]) => `<a href="${h}" class="${k === active ? "on" : ""}">${i}<span>${t}</span></a>`).join("");
  $$('[data-nav="buscar"]').forEach((a) => a.classList.toggle("on", active === "buscar"));
  const w = $("#who");
  w.innerHTML = S.imageTag ? `<img src="${esc(`${BASE}/UserImage?userId=${S.userId}&height=96&tag=${S.imageTag}`)}" alt="" style="width:100%;height:100%;object-fit:cover">`
    : `<span class="avatar" style="--c:${colorFor(S.name)};font-size:1.1rem;border-radius:10px">${esc(S.name.slice(0, 1).toUpperCase())}</span>`;
  w.title = `${S.name}: cambiar de perfil`;
}
addEventListener("scroll", () => $("#bar").classList.toggle("solid", scrollY > 40), { passive: true });

/* ---------------------------------------------------------------- perfiles */
async function viewProfiles(msg = "") {
  chrome(false);
  let users = [];
  try { users = await api("/Users/Public", { quiet: true }) || []; } catch (e) {
    app.innerHTML = `<div class="who-screen"><h1>${esc(CFG.title || "Cine en casa")}</h1>
      <p class="empty">No encuentro el servidor de películas (Jellyfin). Revisa que la PC esté prendida y vuelve a intentar.</p>
      <p><button class="btn play" onclick="location.reload()">Reintentar</button></p></div>`;
    return autofocus();
  }
  const saved = store.get("tokens", {});
  app.innerHTML = `<div class="who-screen"><h1>¿Quién va a ver?</h1>
    ${msg ? `<p class="err">${esc(msg)}</p>` : ""}
    <div class="profiles">${users.map((u) => `<button class="profile" data-id="${u.Id}" data-name="${esc(u.Name)}" data-pw="${u.HasPassword && !saved[u.Id] ? 1 : ""}" data-tag="${u.PrimaryImageTag || ""}">
        <div class="avatar" style="--c:${colorFor(u.Name)}">${u.PrimaryImageTag ? `<img src="${esc(`${BASE}/UserImage?userId=${u.Id}&height=300&tag=${u.PrimaryImageTag}`)}" alt="">` : esc(u.Name.slice(0, 1).toUpperCase())}</div>
        <span>${esc(u.Name)}</span></button>`).join("")}
      <button class="profile" id="other"><div class="avatar" style="--c:var(--ink-3);color:var(--smoke)">+</div><span>Otro usuario</span></button></div>
    <form class="pin" id="pin" hidden><p id="pinwho"></p><input id="pinuser" placeholder="Usuario" autocomplete="username" hidden>
      <input id="pinpw" type="password" placeholder="Contraseña" autocomplete="current-password"><button class="btn play" type="submit">Entrar</button><p class="err" id="pinerr"></p></form></div>`;
  let target = null;
  const form = $("#pin");
  const login = async (name, pw, tag) => {
    try {
      const r = await api("/Users/AuthenticateByName", { method: "POST", body: { Username: name, Pw: pw }, quiet: true });
      S = { userId: r.User.Id, token: r.AccessToken, name: r.User.Name, imageTag: r.User.PrimaryImageTag || tag || "" };
      store.set("session", S);
      const tokens = store.get("tokens", {}); tokens[S.userId] = S; store.set("tokens", tokens);
      location.hash = "#/inicio";
    } catch {
      form.hidden = false; $("#pinerr").textContent = "Contraseña incorrecta. Inténtalo de nuevo."; $("#pinpw").focus();
    }
  };
  $$(".profile[data-id]").forEach((b) => b.onclick = () => {
    const sv = saved[b.dataset.id];
    if (sv) { S = sv; store.set("session", S); location.hash = "#/inicio"; return; }
    target = b.dataset;
    if (!b.dataset.pw) return login(b.dataset.name, "", b.dataset.tag);
    form.hidden = false; $("#pinuser").hidden = true; $("#pinwho").textContent = `Contraseña de ${b.dataset.name}`;
    $("#pinpw").value = ""; $("#pinerr").textContent = ""; $("#pinpw").focus();
  });
  $("#other").onclick = () => { target = null; form.hidden = false; $("#pinuser").hidden = false; $("#pinwho").textContent = "Entra con tu usuario de Jellyfin"; $("#pinuser").focus(); };
  form.onsubmit = (e) => { e.preventDefault(); login(target ? target.name : $("#pinuser").value.trim(), $("#pinpw").value, target?.tag); };
  autofocus();
}
function logout(msg) {
  if (S) { const t = store.get("tokens", {}); delete t[S.userId]; store.set("tokens", t); }
  S = null; store.del("session"); location.hash = "#/perfiles"; if (msg) setTimeout(() => toast(msg), 300);
}

/* ------------------------------------------------------------------ inicio */
async function viewHome() {
  chrome(true, "inicio");
  app.innerHTML = `<div class="hero"><div class="bg"></div></div><div class="rows"><div class="row" style="padding:0 var(--pad)"><div class="spinner"></div></div></div>`;
  const q = `Fields=${FIELDS}&ImageTypeLimit=1&EnableImageTypes=Primary,Backdrop,Thumb,Logo`;
  const [resume, nextup, fav] = await Promise.all([
    api(`/UserItems/Resume?${UID()}&Limit=16&MediaTypes=Video&${q}`).then((r) => r.Items).catch(() => []),
    api(`/Shows/NextUp?UserId=${S.userId}&Limit=16&${q}`).then((r) => r.Items).catch(() => []),
    api(`/Items?${UID()}&Filters=IsFavorite&Recursive=true&IncludeItemTypes=Movie,Series&Limit=20&SortBy=DateCreated&SortOrder=Descending&${q}`).then((r) => r.Items).catch(() => []),
  ]);
  const libs = VIEWS.filter((v) => ["movies", "tvshows"].includes(v.CollectionType));
  const latest = await Promise.all(libs.map((v) => api(`/Items/Latest?${UID()}&ParentId=${v.Id}&Limit=20&${q}`).catch(() => [])));
  const unseen = await Promise.all(libs.map((v) => api(`/Items?${UID()}&ParentId=${v.Id}&Recursive=true&IncludeItemTypes=${v.CollectionType === "movies" ? "Movie" : "Series"}&Filters=IsUnplayed&SortBy=Random&Limit=20&${q}`)
    .then((r) => r.Items).catch(() => [])));
  // Portada: algo con imagen de fondo, distinto cada vez
  const pool = [...latest.flat(), ...unseen.flat()].filter((i) => i.BackdropImageTags?.length && i.Type !== "Episode");
  const hero = pool[Math.floor(Math.random() * pool.length)];
  const nextIds = new Set(nextup.map((i) => i.Id));
  const resumeEp = resume.filter((i) => !nextIds.has(i.Id));
  app.innerHTML = (hero ? heroHTML(hero) : `<div style="height:110px"></div>`) + `<div class="rows">
    ${row("Seguir viendo", resumeEp, { wide: true })}
    ${row("Siguiente episodio", nextup, { wide: true })}
    ${row("Mi lista", fav, { href: "#/milista" })}
    ${libs.map((v, i) => row((v.CollectionType === "movies" ? "Películas recién agregadas" : "Series con capítulos nuevos")
        + (libs.filter((x) => x.CollectionType === v.CollectionType).length > 1 ? ` en ${v.Name}` : ""), latest[i], { href: `#/biblioteca/${v.Id}` })
      + row(v.CollectionType === "movies" ? "Películas que no has visto" : "Series que no has empezado", unseen[i], { href: `#/biblioteca/${v.Id}` })).join("")}
    ${!libs.length ? `<p class="empty" style="padding:0 var(--pad)">Todavía no hay bibliotecas. En Jellyfin agrega una de <b>Películas</b> y otra de <b>Series</b> apuntando a <b>/media</b>.</p>` : ""}
  </div>`;
  if (hero) bindHero(hero);
  autofocus();
}
function heroHTML(it) {
  const logo = logoOf(it), p = pct(it);
  return `<section class="hero"><div class="bg" style="background-image:url('${esc(backdropOf(it))}')"></div>
    <div class="copy">${logo ? `<img class="logo" src="${esc(logo)}" alt="${esc(it.Name)}">` : `<h1 class="marquee">${esc(it.Name)}</h1>`}
      ${facts(it)}<p class="overview">${esc(it.Overview || "")}</p>
      <div class="actions"><button class="btn play" id="hplay">${ICON.play}${p > 1 && p < 99 ? "Continuar" : "Ver ahora"}</button>
        <a class="btn" href="#/item/${it.Id}">${ICON.info}Más información</a></div></div></section>`;
}
function bindHero(it) { $("#hplay").onclick = () => playItem(it); }
async function playItem(it) {
  if (it.Type === "Series") {
    const n = await api(`/Shows/NextUp?UserId=${S.userId}&SeriesId=${it.Id}&Limit=1`).catch(() => null);
    let ep = n?.Items?.[0];
    if (!ep) { const all = await api(`/Shows/${it.Id}/Episodes?UserId=${S.userId}&Limit=1`); ep = all.Items[0]; }
    if (ep) location.hash = `#/ver/${ep.Id}`; else toast("Esta serie todavía no tiene episodios");
    return;
  }
  location.hash = `#/ver/${it.Id}`;
}

/* ----------------------------------------------------------------- detalle */
async function viewItem(id) {
  chrome(true, "");
  const it = await api(`/Items/${id}?${UID()}`);
  if (it.Type === "Episode") return viewItem(it.SeriesId);
  const p = pct(it), resumeAt = it.UserData?.PlaybackPositionTicks || 0;
  const people = (it.People || []);
  const dirs = people.filter((x) => x.Type === "Director").map((x) => x.Name);
  const cast = people.filter((x) => x.Type === "Actor").slice(0, 6).map((x) => x.Name);
  const logo = logoOf(it);
  app.innerHTML = `<section class="detail"><div class="bg" style="background-image:url('${esc(backdropOf(it))}')"></div>
    <div class="detail-grid"><div class="poster" style="background-image:url('${esc(posterOf(it, 720))}')"></div>
    <div>${logo ? `<img class="logo" src="${esc(logo)}" alt="${esc(it.Name)}" style="max-width:min(28rem,80vw);max-height:8rem;object-fit:contain;object-position:left;margin-bottom:1rem">` : `<h1 class="marquee">${esc(it.Name)}</h1>`}
      ${facts(it)}
      ${it.Taglines?.[0] ? `<p style="color:var(--paper);font-weight:600">${esc(it.Taglines[0])}</p>` : ""}
      <p class="overview">${esc(it.Overview || "Sin descripción.")}</p>
      <div class="actions"><button class="btn play" id="dplay">${ICON.play}<span id="dplaytxt">${it.Type === "Series" ? "Ver" : p > 1 && p < 99 ? `Continuar <span class="sub">desde ${clock(resumeAt / TICKS)}</span>` : "Ver ahora"}</span></button>
        ${p > 1 && p < 99 && it.Type !== "Series" ? `<button class="btn" id="dstart">Desde el principio</button>` : ""}
        <button class="btn" id="dfav">${it.UserData?.IsFavorite ? ICON.check + "En mi lista" : ICON.plus + "Mi lista"}</button>
        <button class="btn" id="dseen">${it.UserData?.Played ? ICON.check + "Vista" : "Marcar como vista"}</button></div>
      ${dirs.length || cast.length ? `<div class="people">${dirs.length ? `<div><b>Dirección</b> ${esc(dirs.join(", "))}</div>` : ""}${cast.length ? `<div><b>Reparto</b> ${esc(cast.join(", "))}</div>` : ""}</div>` : ""}
      ${it.Type === "Series" ? `<div class="seasons" id="seasons"></div><div class="episodes" id="eps"></div>` : ""}
    </div></div></section>`;
  $("#dplay").onclick = () => it.Type === "Series" ? playItem(it) : (location.hash = `#/ver/${it.Id}`);
  $("#dstart")?.addEventListener("click", () => { location.hash = `#/ver/${it.Id}?desde=0`; });
  $("#dfav").onclick = async () => {
    const on = !it.UserData?.IsFavorite;
    await api(`/UserFavoriteItems/${it.Id}?${UID()}`, { method: on ? "POST" : "DELETE" });
    it.UserData.IsFavorite = on; $("#dfav").innerHTML = on ? ICON.check + "En mi lista" : ICON.plus + "Mi lista";
    toast(on ? "Agregada a Mi lista" : "Quitada de Mi lista");
  };
  $("#dseen").onclick = async () => {
    const on = !it.UserData?.Played;
    await api(`/UserPlayedItems/${it.Id}?${UID()}`, { method: on ? "POST" : "DELETE" });
    it.UserData.Played = on; $("#dseen").innerHTML = on ? ICON.check + "Vista" : "Marcar como vista";
    if (it.Type === "Series") loadSeasons(it);
  };
  if (it.Type === "Series") {
    const n = await api(`/Shows/NextUp?UserId=${S.userId}&SeriesId=${it.Id}&Limit=1`).catch(() => null);
    const nx = n?.Items?.[0];
    if (nx) $("#dplaytxt").innerHTML = `${(nx.UserData?.PlaybackPositionTicks ? "Continuar" : "Ver")} <span class="sub">${esc(epCode(nx))}</span>`;
    await loadSeasons(it, nx?.ParentIndexNumber);
  }
  autofocus();
}
async function loadSeasons(series, wanted) {
  const s = (await api(`/Shows/${series.Id}/Seasons?UserId=${S.userId}`)).Items;
  if (!s.length) { $("#eps").innerHTML = '<p class="empty">Todavía no hay episodios.</p>'; return; }
  let cur = s.find((x) => x.IndexNumber === wanted) || s.find((x) => x.IndexNumber > 0) || s[0];
  const draw = async () => {
    $("#seasons").innerHTML = s.map((x) => `<button data-s="${x.Id}" class="${x.Id === cur.Id ? "on" : ""}">${esc(x.Name)}</button>`).join("");
    $$("#seasons button").forEach((b) => b.onclick = () => { cur = s.find((x) => x.Id === b.dataset.s); draw(); });
    const eps = (await api(`/Shows/${series.Id}/Episodes?UserId=${S.userId}&SeasonId=${cur.Id}&Fields=Overview,RunTimeTicks`)).Items;
    $("#eps").innerHTML = eps.map((e) => {
      const p = pct(e);
      return `<a class="ep" href="#/ver/${e.Id}"><div class="art" style="background-image:url('${esc(wideOf(e, 480))}')">
        ${p > 1 && p < 99 ? `<div class="progress"><i style="width:${p.toFixed(1)}%"></i></div>` : ""}${e.UserData?.Played ? '<span class="watched" style="position:absolute;top:.4em;right:.4em;width:1.4em;height:1.4em;border-radius:50%;background:var(--gold);color:#1d1405;display:grid;place-items:center;font-size:.75rem;font-weight:800">✓</span>' : ""}</div>
        <div><h3><span class="n">${e.IndexNumber ?? ""}</span>${esc(e.Name)}</h3><p>${esc(e.Overview || "")}</p><div class="len">${runtime(e.RunTimeTicks)}</div></div></a>`;
    }).join("") || '<p class="empty">Esta temporada no tiene episodios.</p>';
  };
  await draw();
}

/* ------------------------------------------------------ biblioteca y listas */
const SORTS = { "DateCreated,SortName:Descending": "Lo más nuevo", "SortName:Ascending": "A–Z", "CommunityRating,SortName:Descending": "Mejor calificadas",
  "PremiereDate,SortName:Descending": "Por año", "Random:Ascending": "Al azar" };
async function viewLibrary(id) {
  const lib = VIEWS.find((v) => v.Id === id);
  if (!lib) return viewHome();
  chrome(true, lib.CollectionType === "movies" ? "peliculas" : "series");
  const type = lib.CollectionType === "movies" ? "Movie" : "Series";
  const st = store.get("lib." + id, { sort: "DateCreated,SortName:Descending", genre: "", unseen: false });
  const others = VIEWS.filter((v) => v.CollectionType === lib.CollectionType && v.Id !== id);
  const genres = (await api(`/Genres?ParentId=${id}&UserId=${S.userId}&SortBy=SortName`).catch(() => ({ Items: [] }))).Items || [];
  app.innerHTML = `<section class="page"><h1>${esc(lib.Name)}</h1>
    ${others.length ? `<div class="filters">${others.map((o) => `<a class="chip" href="#/biblioteca/${o.Id}">${esc(o.Name)}</a>`).join("")}</div>` : ""}
    <div class="filters"><select class="chip" id="sort" aria-label="Ordenar">${Object.entries(SORTS).map(([k, v]) => `<option value="${k}" ${k === st.sort ? "selected" : ""}>${v}</option>`).join("")}</select>
      <button class="chip ${st.unseen ? "on" : ""}" id="unseen">Sin ver</button>
      <button class="chip ${!st.genre ? "on" : ""}" data-g="">Todos</button>
      ${genres.slice(0, 18).map((g) => `<button class="chip ${st.genre === g.Name ? "on" : ""}" data-g="${esc(g.Name)}">${esc(g.Name)}</button>`).join("")}</div>
    <div class="grid" id="grid"></div><button class="btn more" id="more" hidden>Cargar más</button></section>`;
  let start = 0, total = 0;
  const load = async (reset) => {
    if (reset) { start = 0; $("#grid").innerHTML = '<div class="spinner"></div>'; }
    const [sortBy, order] = st.sort.split(":");
    const q = new URLSearchParams({ ParentId: id, Recursive: "true", IncludeItemTypes: type, SortBy: sortBy, SortOrder: order,
      StartIndex: start, Limit: 60, Fields: FIELDS, ImageTypeLimit: "1", EnableImageTypes: "Primary" });
    if (st.genre) q.set("Genres", st.genre);
    if (st.unseen) q.set("Filters", "IsUnplayed");
    const r = await api(`/Items?${UID()}&${q}`);
    total = r.TotalRecordCount; start += r.Items.length;
    const html = r.Items.map((i) => card(i)).join("");
    if (reset) $("#grid").innerHTML = html || `<p class="empty">No hay nada con estos filtros.</p>`; else $("#grid").insertAdjacentHTML("beforeend", html);
    $("#more").hidden = start >= total;
  };
  const save = () => { store.set("lib." + id, st); load(true); };
  $("#sort").onchange = (e) => { st.sort = e.target.value; save(); };
  $("#unseen").onclick = (e) => { st.unseen = !st.unseen; e.currentTarget.classList.toggle("on", st.unseen); save(); };
  $$("[data-g]").forEach((b) => b.onclick = () => { st.genre = b.dataset.g; $$("[data-g]").forEach((x) => x.classList.toggle("on", x === b)); save(); });
  $("#more").onclick = () => load(false);
  await load(true);
  autofocus();
}
async function viewMyList() {
  chrome(true, "milista");
  const r = await api(`/Items?${UID()}&Filters=IsFavorite&Recursive=true&IncludeItemTypes=Movie,Series&SortBy=SortName&Fields=${FIELDS}`);
  app.innerHTML = `<section class="page"><h1>Mi lista</h1>${r.Items.length ? `<div class="grid">${r.Items.map((i) => card(i)).join("")}</div>`
    : `<p class="empty">Aquí aparece lo que guardes para después. Abre una película o serie y elige <b>Mi lista</b>.</p>`}</section>`;
  autofocus();
}
async function viewSearch() {
  chrome(true, "buscar");
  app.innerHTML = `<section class="page"><h1>Buscar</h1><input class="search" id="q" type="search" placeholder="Película, serie o actor" autocomplete="off" enterkeyhint="search">
    <div id="res"><p class="empty">Escribe al menos dos letras.</p></div></section>`;
  const q = $("#q"); let t;
  q.value = store.get("lastq", "");
  const run = async () => {
    const v = q.value.trim(); store.set("lastq", v);
    if (v.length < 2) { $("#res").innerHTML = '<p class="empty">Escribe al menos dos letras.</p>'; return; }
    const r = await api(`/Items?${UID()}&searchTerm=${encodeURIComponent(v)}&Recursive=true&IncludeItemTypes=Movie,Series&Limit=48&Fields=${FIELDS}`);
    $("#res").innerHTML = r.Items.length ? `<div class="grid">${r.Items.map((i) => card(i)).join("")}</div>`
      : `<p class="empty">Nada con «${esc(v)}». Prueba con otra palabra del título.</p>`;
  };
  q.oninput = () => { clearTimeout(t); t = setTimeout(run, 300); };
  if (q.value) run();
  q.focus();
}

/* ------------------------------------------------------------- reproductor */
const PLAYER = { report: null };
function deviceProfile() {
  const v = document.createElement("video");
  const can = (t) => !!(v.canPlayType(t).replace("no", "") || (window.MediaSource?.isTypeSupported?.(t)));
  const hevc = can('video/mp4; codecs="hvc1.1.6.L93.B0"') || can('video/mp4; codecs="hev1.1.6.L93.B0"');
  const av1 = can('video/mp4; codecs="av01.0.05M.08"');
  const ac3 = can('audio/mp4; codecs="ac-3"'), eac3 = can('audio/mp4; codecs="ec-3"');
  const safari = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
  const vcodecs = ["h264", hevc && "hevc", av1 && "av1", "vp9"].filter(Boolean).join(",");
  const acodecs = ["aac", "mp3", "opus", "flac", ac3 && "ac3", eac3 && "eac3"].filter(Boolean).join(",");
  return {
    MaxStreamingBitrate: CFG.maxBitrate || 20000000, MaxStaticBitrate: 100000000, MusicStreamingTranscodingBitrate: 384000,
    DirectPlayProfiles: [
      { Container: "mp4,m4v", Type: "Video", VideoCodec: vcodecs, AudioCodec: acodecs },
      { Container: "webm", Type: "Video", VideoCodec: "vp8,vp9,av1", AudioCodec: "vorbis,opus" },
      ...(safari ? [{ Container: "mov", Type: "Video", VideoCodec: "h264,hevc", AudioCodec: acodecs }]
        : [{ Container: "mkv", Type: "Video", VideoCodec: "h264,vp9,av1" + (hevc ? ",hevc" : ""), AudioCodec: "aac,mp3,opus,flac,vorbis" }]),
    ],
    TranscodingProfiles: [{ Container: "ts", Type: "Video", VideoCodec: "h264" + (hevc ? ",hevc" : ""), AudioCodec: "aac" + (ac3 ? ",ac3" : ""),
      Context: "Streaming", Protocol: "hls", MaxAudioChannels: "2", MinSegments: "1", BreakOnNonKeyFrames: true }],
    ContainerProfiles: [], CodecProfiles: [],
    SubtitleProfiles: [{ Format: "vtt", Method: "External" }, { Format: "srt", Method: "External" }, { Format: "subrip", Method: "External" },
      { Format: "ass", Method: "External" }, { Format: "ssa", Method: "External" }, { Format: "pgssub", Method: "Encode" },
      { Format: "dvdsub", Method: "Encode" }, { Format: "dvbsub", Method: "Encode" }],
  };
}
function loadHls() {
  if (window.Hls) return Promise.resolve(window.Hls);
  return new Promise((ok, fail) => {
    const s = document.createElement("script"); s.src = CFG.hlsjs; s.onload = () => ok(window.Hls);
    s.onerror = () => fail(new Error("No pude cargar el reproductor HLS (¿sin internet?).")); document.head.append(s);
  });
}

async function viewPlayer(id, startOverride) {
  chrome(false);
  const it = await api(`/Items/${id}?${UID()}`);
  const startTicks = startOverride != null ? startOverride * TICKS : (it.UserData?.PlaybackPositionTicks || 0);
  app.innerHTML = `<div class="player idle" id="pl"><video id="v" playsinline preload="auto" crossorigin="anonymous"></video>
    <div class="ui"><div class="top"><button class="cbtn" id="pback" aria-label="Salir">${ICON.back}</button>
      <div class="t">${esc(it.Type === "Episode" ? it.SeriesName : it.Name)}${it.Type === "Episode" ? `<small>${esc(epCode(it))} ${esc(it.Name)}</small>` : ""}</div></div>
      <div class="mid"><div class="spinner" id="spin"></div><button class="bigplay" id="bigplay" hidden aria-label="Reproducir">${ICON.play}</button></div>
      <div class="bottom"><div class="seek" id="seek" tabindex="0" role="slider" aria-label="Posición"><div class="rail"><div class="buf" id="buf"></div><div class="pos" id="pos"></div><div class="knob" id="knob"></div></div></div>
        <div class="ctrls"><button class="cbtn" id="pp" aria-label="Pausa">${ICON.pause}</button>
          <button class="cbtn" id="rw" aria-label="Atrás 10 segundos">${ICON.rew}</button><button class="cbtn" id="ff" aria-label="Adelante 10 segundos">${ICON.fwd}</button>
          <span class="time" id="time">0:00 / ${clock((it.RunTimeTicks || 0) / TICKS)}</span><span class="sp"></span>
          <button class="cbtn" id="cc" aria-label="Subtítulos" hidden>${ICON.cc}</button><button class="cbtn" id="au" aria-label="Idioma" hidden>${ICON.audio}</button>
          <button class="cbtn" id="nx" aria-label="Siguiente episodio" hidden>${ICON.next}</button><button class="cbtn hide-s" id="fs" aria-label="Pantalla completa">${ICON.full}</button></div></div></div>
    <div id="menu"></div><div id="upnext"></div></div>`;
  const pl = $("#pl"), v = $("#v");
  const P = { it, ms: null, session: null, method: "DirectPlay", hls: null, audio: null, sub: -1, offset: 0, duration: (it.RunTimeTicks || 0) / TICKS, next: null, ended: false };
  PLAYER.cur = P;

  async function open(fromTicks, { audio, sub, forceTranscode } = {}) {
    $("#spin").hidden = false;
    if (P.hls) { P.hls.destroy(); P.hls = null; }
    if (P.session && P.method === "Transcode") api(`/Videos/ActiveEncodings?deviceId=${deviceId}&playSessionId=${P.session}`, { method: "DELETE", quiet: true }).catch(() => {});
    const body = { UserId: S.userId, DeviceProfile: deviceProfile(), StartTimeTicks: Math.floor(fromTicks), AutoOpenLiveStream: true,
      MaxStreamingBitrate: CFG.maxBitrate || 20000000, EnableDirectPlay: !forceTranscode, EnableDirectStream: !forceTranscode, EnableTranscoding: true };
    if (audio != null) body.AudioStreamIndex = audio;
    if (sub != null && sub >= 0) body.SubtitleStreamIndex = sub;
    const info = await api(`/Items/${it.Id}/PlaybackInfo?UserId=${S.userId}`, { method: "POST", body });
    if (info.ErrorCode) throw new Error(info.ErrorCode === "NotAllowed" ? "Tu perfil no puede ver esto." : `Jellyfin: ${info.ErrorCode}`);
    const ms = P.ms = info.MediaSources[0]; P.session = info.PlaySessionId;
    P.audio = audio ?? ms.DefaultAudioStreamIndex;
    let url;
    if (ms.SupportsDirectPlay && !forceTranscode) {
      P.method = "DirectPlay"; P.offset = 0;
      url = `${BASE}/Videos/${it.Id}/stream?static=true&mediaSourceId=${ms.Id}&deviceId=${deviceId}&ApiKey=${S.token}&Tag=${ms.ETag || ""}`;
    } else if (ms.TranscodingUrl) {
      P.method = "Transcode"; P.offset = 0;
      url = BASE + ms.TranscodingUrl;
    } else throw new Error("Este video no se puede reproducir en este navegador.");
    v.querySelectorAll("track").forEach((t) => t.remove());
    const subs = ms.MediaStreams.filter((s) => s.Type === "Subtitle");
    subs.filter((s) => (s.DeliveryMethod === "External" || s.IsTextSubtitleStream) && s.DeliveryMethod !== "Encode").forEach((s) => {
      const tr = document.createElement("track");
      tr.kind = "subtitles"; tr.label = s.DisplayTitle || s.Language || `Subtítulo ${s.Index}`; tr.srclang = (s.Language || "und").slice(0, 3);
      tr.src = s.DeliveryUrl ? BASE + s.DeliveryUrl : `${BASE}/Videos/${it.Id}/${ms.Id}/Subtitles/${s.Index}/0/Stream.vtt?ApiKey=${S.token}`;
      tr.dataset.index = s.Index; v.append(tr);
    });
    const isHls = /\.m3u8/i.test(url);
    if (isHls && !v.canPlayType("application/vnd.apple.mpegurl")) {
      const Hls = await loadHls();
      P.hls = new Hls({ startPosition: fromTicks / TICKS, maxBufferLength: 30 });
      P.hls.loadSource(url); P.hls.attachMedia(v);
      P.hls.on(Hls.Events.ERROR, (_e, d) => { if (d.fatal) { toast("Se cortó el video; reintentando…"); P.hls.recoverMediaError(); } });
    } else {
      v.src = url;
      v.addEventListener("loadedmetadata", () => { if (fromTicks) v.currentTime = fromTicks / TICKS; }, { once: true });
    }
    // subtítulo inicial
    const want = sub ?? (ms.DefaultSubtitleStreamIndex ?? -1);
    P.sub = want;
    v.addEventListener("loadedmetadata", () => setSubtitle(P.sub), { once: true });
    $("#cc").hidden = !subs.length;
    $("#au").hidden = ms.MediaStreams.filter((s) => s.Type === "Audio").length < 2;
    await v.play().catch(() => { $("#bigplay").hidden = false; });
    report("Playing");
  }
  function setSubtitle(idx) {
    P.sub = idx;
    [...v.textTracks].forEach((t, i) => { const tr = v.querySelectorAll("track")[i]; t.mode = tr && +tr.dataset.index === idx ? "showing" : "disabled"; });
  }
  const ticksNow = () => Math.floor((v.currentTime + P.offset) * TICKS);
  function report(kind) {
    if (!P.ms) return;
    const body = { ItemId: it.Id, MediaSourceId: P.ms.Id, PlaySessionId: P.session, PositionTicks: ticksNow(), IsPaused: v.paused,
      CanSeek: true, PlayMethod: P.method, AudioStreamIndex: P.audio, SubtitleStreamIndex: P.sub };
    const path = kind === "Playing" ? "/Sessions/Playing" : kind === "Stopped" ? "/Sessions/Playing/Stopped" : "/Sessions/Playing/Progress";
    if (kind === "Stopped" && navigator.sendBeacon) {        // al cerrar la pestaña también se guarda
      fetch(BASE + path, { method: "POST", keepalive: true, headers: { "Authorization": authHeader(), "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => {});
      return;
    }
    api(path, { method: "POST", body, quiet: true }).catch(() => {});
  }
  clearInterval(PLAYER.report);
  PLAYER.report = setInterval(() => !v.paused && report("Progress"), 10000);
  PLAYER.stop = () => {
    clearInterval(PLAYER.report); report("Stopped");
    if (P.method === "Transcode" && P.session) api(`/Videos/ActiveEncodings?deviceId=${deviceId}&playSessionId=${P.session}`, { method: "DELETE", quiet: true }).catch(() => {});
    if (P.hls) P.hls.destroy();
    v.removeAttribute("src"); v.load();
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    PLAYER.cur = null;
  };

  // --- interfaz
  let idleT;
  const wake = () => { pl.classList.remove("idle"); clearTimeout(idleT); idleT = setTimeout(() => { if (!v.paused && !$("#menu").innerHTML) pl.classList.add("idle"); }, 3200); };
  ["mousemove", "touchstart", "keydown"].forEach((e) => pl.addEventListener(e, wake, { passive: true }));
  const dur = () => P.duration || v.duration || 0;
  const paint = () => {
    const t = v.currentTime + P.offset, d = dur();
    $("#pos").style.width = $("#knob").style.left = (d ? t / d * 100 : 0) + "%";
    if (v.buffered.length) $("#buf").style.width = (d ? (v.buffered.end(v.buffered.length - 1) + P.offset) / d * 100 : 0) + "%";
    $("#time").textContent = `${clock(t)} / ${clock(d)}`;
    if (P.next && d && d - t < 30 && !$("#upnext").innerHTML) showUpNext();
  };
  v.addEventListener("timeupdate", paint);
  v.addEventListener("waiting", () => { $("#spin").hidden = false; });
  v.addEventListener("playing", () => { $("#spin").hidden = true; $("#bigplay").hidden = true; $("#pp").innerHTML = ICON.pause; wake(); });
  v.addEventListener("pause", () => { $("#pp").innerHTML = ICON.play; pl.classList.remove("idle"); report("Progress"); });
  v.addEventListener("ended", () => { P.ended = true; report("Stopped"); if (P.next) location.replace(`#/ver/${P.next.Id}`); else history.back(); });
  v.addEventListener("error", () => { if (P.method === "DirectPlay") { toast("Convirtiendo el video para este equipo…"); open(ticksNow(), { forceTranscode: true }); } else toast("No se pudo reproducir este video."); });
  const toggle = () => (v.paused ? v.play() : v.pause());
  const seekBy = (s) => { v.currentTime = Math.max(0, Math.min(dur() - 1, v.currentTime + s)); paint(); wake(); };
  $("#pp").onclick = toggle; $("#bigplay").onclick = () => v.play();
  $("#rw").onclick = () => seekBy(-10); $("#ff").onclick = () => seekBy(10);
  v.onclick = () => { if (matchMedia("(pointer: fine)").matches) toggle(); else wake(); };
  v.ondblclick = () => fullscreen();
  $("#pback").onclick = () => history.back();
  const fullscreen = () => document.fullscreenElement ? document.exitFullscreen() : pl.requestFullscreen?.().catch(() => {});
  $("#fs").onclick = fullscreen;
  const seekEl = $("#seek");
  const seekTo = (e) => { const r = seekEl.getBoundingClientRect(); v.currentTime = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * dur(); paint(); };
  seekEl.addEventListener("pointerdown", (e) => { seekTo(e); seekEl.setPointerCapture(e.pointerId); seekEl.onpointermove = seekTo; });
  seekEl.addEventListener("pointerup", () => { seekEl.onpointermove = null; });
  seekEl.addEventListener("keydown", (e) => { if (e.key === "ArrowLeft") { seekBy(-10); e.preventDefault(); e.stopPropagation(); } if (e.key === "ArrowRight") { seekBy(10); e.preventDefault(); e.stopPropagation(); } });

  function menu(title, opts, onpick) {
    const m = $("#menu");
    if (m.innerHTML) { m.innerHTML = ""; return; }
    m.innerHTML = `<div class="menu"><h4>${esc(title)}</h4>${opts.map((o) => `<button data-v="${o.v}" class="${o.on ? "on" : ""}">${esc(o.t)}</button>`).join("")}</div>`;
    $$("#menu button").forEach((b) => b.onclick = () => { m.innerHTML = ""; onpick(+b.dataset.v); });
    ($("#menu button.on") || $("#menu button")).focus();
  }
  $("#cc").onclick = () => {
    const subs = P.ms.MediaStreams.filter((s) => s.Type === "Subtitle");
    menu("Subtítulos", [{ v: -1, t: "Sin subtítulos", on: P.sub === -1 }, ...subs.map((s) => ({ v: s.Index, t: s.DisplayTitle || s.Language || `Pista ${s.Index}`, on: s.Index === P.sub }))], (idx) => {
      const s = subs.find((x) => x.Index === idx);
      if (idx === -1 || v.querySelector(`track[data-index="${idx}"]`)) setSubtitle(idx);
      else open(ticksNow(), { audio: P.audio, sub: idx, forceTranscode: true });       // subtítulo de imagen: va «quemado»
      toast(idx === -1 ? "Subtítulos apagados" : `Subtítulos: ${s?.DisplayTitle || s?.Language}`);
    });
  };
  $("#au").onclick = () => {
    const aud = P.ms.MediaStreams.filter((s) => s.Type === "Audio");
    menu("Idioma", aud.map((s) => ({ v: s.Index, t: s.DisplayTitle || s.Language || `Pista ${s.Index}`, on: s.Index === P.audio })), (idx) => {
      // el navegador no cambia de pista de audio por sí solo: Jellyfin manda la elegida
      if (idx !== P.audio) open(ticksNow(), { audio: idx, sub: P.sub, forceTranscode: true });
    });
  };
  // --- siguiente episodio
  if (it.Type === "Episode") {
    api(`/Shows/${it.SeriesId}/Episodes?UserId=${S.userId}&StartItemId=${it.Id}&Limit=2&Fields=Overview`).then((r) => {
      P.next = r.Items.find((x) => x.Id !== it.Id) || null;
      $("#nx").hidden = !P.next;
      $("#nx").onclick = () => location.replace(`#/ver/${P.next.Id}`);
    }).catch(() => {});
  }
  function showUpNext() {
    const n = P.next;
    $("#upnext").innerHTML = `<div class="upnext"><div class="art" style="background-image:url('${esc(wideOf(n, 360))}')"></div>
      <div><b>Sigue: ${esc(epCode(n))} ${esc(n.Name)}</b><button class="btn play" id="ungo">${ICON.play}Ver ahora</button></div></div>`;
    $("#ungo").onclick = () => location.replace(`#/ver/${n.Id}`);
  }
  // --- teclas del reproductor (control de tele incluido)
  PLAYER.keys = (e) => {
    const k = e.key;
    if ($("#menu").innerHTML && (k === "Escape" || k === "Backspace" || k === "GoBack" || e.keyCode === 461 || e.keyCode === 10009)) { $("#menu").innerHTML = ""; e.preventDefault(); return true; }
    const hidden = pl.classList.contains("idle");
    if (k === " " || k === "MediaPlayPause" || k === "k") { toggle(); wake(); e.preventDefault(); return true; }
    if (k === "MediaPlay") { v.play(); return true; }
    if (k === "MediaPause" || k === "MediaStop") { v.pause(); return true; }
    if (k === "MediaFastForward" || k === "MediaTrackNext") { seekBy(30); return true; }
    if (k === "MediaRewind" || k === "MediaTrackPrevious") { seekBy(-30); return true; }
    if (hidden && (k === "ArrowLeft" || k === "ArrowRight")) { seekBy(k === "ArrowLeft" ? -10 : 10); e.preventDefault(); return true; }
    if (hidden && (k === "Enter" || k === "ArrowUp" || k === "ArrowDown")) { wake(); $("#pp").focus(); e.preventDefault(); return true; }
    if (k === "f") { fullscreen(); return true; }
    return false;
  };
  try {
    if (TV) pl.requestFullscreen?.().catch(() => {});
    await open(startTicks);
  } catch (e) {
    $("#spin").hidden = true;
    $(".mid").innerHTML = `<div style="text-align:center;max-width:34rem;padding:1rem"><p style="font-size:1.2rem">${esc(e.message)}</p>
      <button class="btn" onclick="history.back()">Regresar</button></div>`;
  }
  wake();
}

/* ------------------------------------------------- navegación con control */
const FOCUSABLE = "a[href], button:not([disabled]), input, select, [tabindex='0']";
function visibleFocusables() {
  const scope = $(".player") || $("#menu .menu") && $(".player") || document;
  return $$(FOCUSABLE, scope).filter((el) => {
    if (el.closest("[hidden]") || el.getAttribute("tabindex") === "-1") return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden";
  });
}
function move(dir) {
  const cur = document.activeElement;
  const all = visibleFocusables();
  if (!cur || cur === document.body || !all.includes(cur)) { (all[0])?.focus(); return; }
  const a = cur.getBoundingClientRect(), ac = { x: a.left + a.width / 2, y: a.top + a.height / 2 };
  let best = null, bestScore = Infinity;
  for (const el of all) {
    if (el === cur) continue;
    const b = el.getBoundingClientRect(), bc = { x: b.left + b.width / 2, y: b.top + b.height / 2 };
    let main, cross;
    const sameLine = b.top < a.bottom - 4 && b.bottom > a.top + 4;      // izquierda/derecha: solo en la misma fila
    if (dir === "ArrowRight") { if (b.left < a.right - 2 || !sameLine) continue; main = b.left - a.right; cross = Math.abs(bc.y - ac.y); }
    else if (dir === "ArrowLeft") { if (b.right > a.left + 2 || !sameLine) continue; main = a.left - b.right; cross = Math.abs(bc.y - ac.y); }
    else if (dir === "ArrowDown") { if (b.top < a.bottom - 2) continue; main = b.top - a.bottom; cross = Math.abs(bc.x - ac.x); }
    else { if (b.bottom > a.top + 2) continue; main = a.top - b.bottom; cross = Math.abs(bc.x - ac.x); }
    // en vertical, preferimos quedarnos alineados con la columna de la izquierda (empezar las filas)
    const score = main + cross * (dir === "ArrowUp" || dir === "ArrowDown" ? 0.6 : 2.5);
    if (score < bestScore) { bestScore = score; best = el; }
  }
  if (best) {
    best.focus({ preventScroll: true });
    const track = best.closest(".track");
    if (track) {
      const r = best.getBoundingClientRect(), tr = track.getBoundingClientRect(), pad = parseFloat(getComputedStyle(track).paddingLeft);
      if (r.left < tr.left + pad || r.right > tr.right - pad) track.scrollBy({ left: r.left - tr.left - pad, behavior: "smooth" });
    }
    const r = best.getBoundingClientRect();
    if (best.closest(".hero")) scrollTo({ top: 0, behavior: "smooth" });
    else if (r.top < innerHeight * 0.18 || r.bottom > innerHeight * 0.9) scrollBy({ top: r.top - innerHeight * 0.38, behavior: "smooth" });
  }
}
// Al regresar (Atrás) el foco vuelve a la tarjeta donde estabas
const LAST = {};
document.addEventListener("focusin", (e) => {
  const c = e.target.closest?.(".card, .ep");
  if (c) LAST[location.hash] = c.getAttribute("href");
});
function autofocus() {
  const back = LAST[location.hash] && $(`.card[href="${LAST[location.hash]}"], .ep[href="${LAST[location.hash]}"]`);
  if (back && (TV || document.body.dataset.keys)) {
    back.focus({ preventScroll: true });
    back.closest(".track")?.scrollBy({ left: back.getBoundingClientRect().left - back.closest(".track").getBoundingClientRect().left - 60 });
    back.scrollIntoView({ block: "center" });
    return;
  }
  if (!TV) return;
  const el = $("#hplay") || $("#dplay") || $(".profile") || $(".card") || visibleFocusables()[0];
  el?.focus({ preventScroll: true });
}
addEventListener("keydown", (e) => { if (e.key.startsWith("Arrow")) document.body.dataset.keys = "1"; }, { capture: true });
addEventListener("pointerdown", () => { delete document.body.dataset.keys; }, { capture: true });
const BACK_KEYS = new Set(["Escape", "Backspace", "GoBack", "BrowserBack"]);
addEventListener("keydown", (e) => {
  if (PLAYER.cur && PLAYER.keys?.(e)) return;
  const typing = e.target.matches?.("input:not([type=checkbox]), textarea, select");
  if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(e.key)) {
    if (typing && (e.key === "ArrowLeft" || e.key === "ArrowRight")) return;
    if (e.target.tagName === "SELECT" && (e.key === "ArrowUp" || e.key === "ArrowDown")) return;
    e.preventDefault(); move(e.key); return;
  }
  if ((BACK_KEYS.has(e.key) || e.keyCode === 461 || e.keyCode === 10009) && !(typing && e.key === "Backspace")) {
    if (location.hash.startsWith("#/inicio") || location.hash.startsWith("#/perfiles")) return;
    e.preventDefault(); history.back();
  }
});

/* ------------------------------------------------------------------- rutas */
async function route() {
  if (PLAYER.cur) PLAYER.stop();            // salir del reproductor (o pasar al siguiente episodio)
  const [path, query] = location.hash.slice(2).split("?");
  const [name, id] = path.split("/");
  if (!S || name === "perfiles") { if (name !== "perfiles") return (location.hash = "#/perfiles"); return viewProfiles(); }
  if (!VIEWS.length) {
    try { VIEWS = (await api(`/UserViews?${UID()}`)).Items || []; } catch (e) { if (e.message !== "401") app.innerHTML = `<p class="page empty">${esc(e.message)}</p>`; return; }
  }
  scrollTo(0, 0);
  try {
    if (name === "item") await viewItem(id);
    else if (name === "biblioteca") await viewLibrary(id);
    else if (name === "ver") await viewPlayer(id, new URLSearchParams(query || "").get("desde"));
    else if (name === "buscar") await viewSearch();
    else if (name === "milista") await viewMyList();
    else await viewHome();
  } catch (e) {
    if (e.message !== "401") app.innerHTML = `<section class="page"><h1>Algo falló</h1><p class="empty">${esc(e.message)}</p><a class="btn" href="#/inicio">Ir al inicio</a></section>`;
  }
}
addEventListener("hashchange", route);
addEventListener("pagehide", () => PLAYER.cur && PLAYER.stop());
if (!location.hash) location.hash = S ? "#/inicio" : "#/perfiles"; else route();
