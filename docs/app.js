/* La Mañana: lector del digest diario generado por GitHub Actions. */
(() => {
  "use strict";

  const $ = (s, el = document) => el.querySelector(s);
  const store = {
    get(k, d) { try { const v = localStorage.getItem("lm:" + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("lm:" + k, JSON.stringify(v)); } catch {} },
  };

  const state = {
    days: [],
    dayIdx: 0,
    digest: null,
    section: store.get("section", "top") === "todas" ? "top" : store.get("section", "top"),
    read: new Set(store.get("read", [])),
    hiddenSources: new Set(store.get("hiddenSources", [])),
    hiddenTopics: new Set(store.get("hiddenTopics", [])),
    query: "",
    archive: null, // ediciones de días anteriores, cargadas solo si se buscan
    prefs: Object.assign({ theme: "auto", size: "m", hideRead: false, showImages: true }, store.get("prefs", {})),
  };

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");
  const tz = "Europe/Madrid";
  const fmtTime = (iso) => new Date(iso).toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit", timeZone: tz });
  const fmtShort = (ymd) => new Date(ymd + "T12:00:00").toLocaleDateString("es-ES", { weekday: "short", day: "numeric", month: "short" });
  const fmtDay = (ymd) => new Date(ymd + "T12:00:00").toLocaleDateString("es-ES", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  function ago(iso) {
    const h = (Date.now() - new Date(iso)) / 36e5;
    if (h < 1) return `hace ${Math.max(1, Math.round(h * 60))} min`;
    if (h < 24) return `hace ${Math.round(h)} h`;
    return fmtTime(iso);
  }
  function showError(err) {
    const msg = (err && (err.message || err.reason?.message || err.reason)) || String(err);
    console.error(err);
    toast("Error: " + String(msg).slice(0, 140));
  }
  window.addEventListener("error", (e) => showError(e.error || e.message));
  window.addEventListener("unhandledrejection", (e) => showError(e.reason));

  function toast(msg) {
    const t = $("#toast"); t.textContent = msg; t.classList.add("show");
    clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 2200);
  }

  /* ----------------------------------------------------------- preferencias */
  function applyPrefs() {
    document.documentElement.dataset.theme = state.prefs.theme;
    document.documentElement.dataset.size = state.prefs.size;
    store.set("prefs", state.prefs);
  }

  /* ----------------------------------------------------------- datos */
  async function getJSON(path) {
    const r = await fetch(path, { cache: "no-cache" });
    if (!r.ok) throw new Error(r.status + " " + path);
    return r.json();
  }

  async function loadIndex() {
    try { state.days = (await getJSON("data/index.json")).days || []; } catch { state.days = []; }
    try { state.podcast = await getJSON("data/podcast.json"); } catch { state.podcast = null; }
  }

  async function loadDay(idx) {
    const day = state.days[idx];
    const path = idx === 0 || !day ? "data/latest.json" : `data/${day}.json`;
    try {
      state.digest = await getJSON(path);
      state.dayIdx = idx;
    } catch (e) {
      if (!state.digest) $("#main").innerHTML = `<p class="empty">No hay edición disponible todavía.<br>Ejecuta el workflow una vez desde GitHub → Actions.</p>`;
      toast("Sin conexión: mostrando lo último guardado");
      return;
    }
    render();
  }

  /* ----------------------------------------------------------- render */
  const episodeFor = (date) => state.podcast?.episodes?.find((e) => e.date === date) || null;

  // Noticia por id: de la edición o, si ya no está en ella, de la ficha guardada con el podcast
  function itemOf(id) {
    const it = state.digest.items[id];
    if (it) return it;
    return episodeFor(state.digest.date)?.story_items?.find((s) => s.id === id) || null;
  }

  // Secciones de la edición + la pestaña virtual "Podcast" si ese día hay episodio
  function viewSections() {
    const secs = state.digest.sections.slice();
    const ep = episodeFor(state.digest.date);
    if (ep?.stories?.length) {
      secs.splice(1, 0, { id: "podcast", name: "En el podcast de hoy", short: "Podcast", items: ep.stories, virtual: true });
    }
    secs.unshift({ id: "todas", name: "Todas", short: "Todas", items: mixedIds(), virtual: true });
    return secs;
  }

  // Todas las noticias de la edición, de mayor a menor relevancia pero mezclando
  // temas: nunca dos seguidas del mismo tema si hay otra cerca para alternar.
  function mixedIds() {
    const all = Object.entries(state.digest.items)
      .map(([id, it]) => ({ id, score: it.score || 0, topic: it.topics?.[0] || "_" }))
      .sort((a, b) => b.score - a.score);
    const out = [];
    const recent = [];  // temas de las últimas 3 tarjetas
    while (all.length) {
      // la más relevante (entre las 12 siguientes) cuyo tema no haya salido en las 3 últimas
      let k = all.findIndex((x, i) => i < 12 && !recent.includes(x.topic));
      if (k < 0) k = all.findIndex((x, i) => i < 12 && x.topic !== recent[recent.length - 1]);
      if (k < 0) k = 0;
      const [x] = all.splice(k, 1);
      out.push(x.id);
      recent.push(x.topic);
      if (recent.length > 3) recent.shift();
    }
    return out;
  }

  function visibleItems(ids, { allTopics = false } = {}) {
    return ids
      .map((id) => ({ ...itemOf(id), id }))
      .filter((it) => it.title && (allTopics || !state.hiddenSources.has(it.source_id)))
      // fuera si todos sus temas están ocultos (las que no tienen tema se quedan)
      .filter((it) => allTopics || !(it.topics?.length && it.topics.every((t) => state.hiddenTopics.has(t))))
      .filter((it) => !(state.prefs.hideRead && state.read.has(it.id)));
  }

  function renderHeader() {
    const d = state.digest;
    const ok = d.sources.filter((s) => s.ok).length;
    $("#edition").textContent = `Edición ${fmtTime(d.generated_at)} · ${ok}/${d.sources.length} medios`;
    $("#dateline").textContent = fmtDay(d.date);
    $("#prevDay").disabled = state.dayIdx >= state.days.length - 1;
    if (state.dayIdx > 0) $("#edition").innerHTML = `<button class="today-btn" id="goToday">Volver a hoy ›</button>`;
  }

  function renderTabs() {
    const secs = viewSections().filter((s) => s.items.length && (s.virtual || !state.hiddenTopics.has(s.id)));
    if (!secs.find((s) => s.id === state.section)) state.section = "top";
    $("#tabs").innerHTML = secs
      .map((s) => {
        const unread = s.items.filter((id) => !state.read.has(id)).length;
        const sel = !state.query && s.id === state.section;
        const tab = `<button class="tab" role="tab" data-id="${esc(s.id)}" aria-selected="${sel}">${esc(s.short || s.name)}<span class="count">${unread}</span></button>`;
        return s.id === "todas" ? tab + `<span class="tab-sep" aria-hidden="true">|</span>` : tab;
      })
      .join("");
    $(`.tab[aria-selected="true"]`)?.scrollIntoView({ inline: "center", block: "nearest" });
  }

  function storyHTML(it, i, lead) {
    const read = state.read.has(it.id);
    const img = state.prefs.showImages && it.image ? `<img class="thumb" src="${esc(safeUrl(it.image))}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">` : "";
    const text = it.ai_summary ? `<p class="ai">${esc(it.ai_summary)}</p>` : it.summary ? `<p>${esc(it.summary)}</p>` : "";
    const also = it.also?.length
      ? `<div class="also"><span>También en</span>${it.also.map((a) => `<a href="${esc(safeUrl(a.url))}" target="_blank" rel="noopener" data-read="${esc(it.id)}" title="${esc(a.title)}">${esc(a.source)}</a>`).join("")}</div>`
      : "";
    return `<article class="story reveal${lead ? " lead" : ""}${read ? " read" : ""}" style="--i:${i}" id="s-${esc(it.id)}">
      <a class="hit" href="${esc(safeUrl(it.url))}" target="_blank" rel="noopener" data-read="${esc(it.id)}" aria-label="${esc(it.title)}"></a>
      <div class="meta"><span class="num">${String(i + 1).padStart(2, "0")}</span><span class="src">${esc(it.source)}</span><span class="dot"></span>${it._day ? `<span class="day">${esc(fmtShort(it._day))}</span>` : `<time datetime="${esc(it.published)}">${ago(it.published)}</time>`}${it._rel ? `<span class="dot"></span><span class="rel">Relevancia ${esc(it._rel)}</span>` : ""}${read ? '<span class="read-mark">Leída ✓</span>' : ""}</div>
      ${lead ? img : ""}
      <h3>${esc(it.title)}</h3>
      ${lead ? "" : img}
      ${text}
      ${also}
    </article>`;
  }

  // Reproductor fuera del contenido: sobrevive a cambios de pestaña y redibujados.
  // Se ve en Portada y Podcast, y en cualquier pestaña mientras suena.
  function renderPodcastBox() {
    const box = $("#podcastBox");
    const audio = $("#podcastBox audio");
    const playing = audio && !audio.paused;
    const ep = episodeFor(state.digest.date);
    if (ep && box.dataset.file !== ep.file && !playing) {
      const mins = Math.max(1, Math.round(ep.duration / 60));
      box.dataset.file = ep.file;
      box.innerHTML = `<section class="podcast">
        <span class="kicker">Podcast · ${mins} min</span>
        <h2>${esc(ep.title)}</h2>
        <audio controls preload="none" src="podcast/${esc(ep.file)}"></audio>
        <p class="pod-topics">${esc((ep.topic_names || []).join(" · "))}</p>
        ${ep.transcript?.length ? `<details><summary>Leer el guion</summary>${ep.transcript.map((t) => `<p><b>${esc(t.speaker)}</b> ${esc(t.text)}</p>`).join("")}</details>` : ""}
      </section>`;
      $("#podcastBox audio").addEventListener("pause", renderPodcastBox);
    }
    const here = !state.query && (state.section === "top" || state.section === "podcast");
    box.hidden = !box.dataset.file || !(here && (ep?.file === box.dataset.file) || ($("#podcastBox audio") && !$("#podcastBox audio").paused));
  }

  function renderMain() {
    if (state.query) { dropFeed(); return renderSearch(); }
    if (state.section === "todas") {
      renderSection("top"); // la Portada queda debajo: es lo que se ve al deslizar para salir
      const feed = $("#feed");
      if (!feed || feed.dataset.edition !== state.digest.generated_at) renderFeed(viewSections().find((s) => s.id === "todas"));
      return;
    }
    dropFeed();
    renderSection(state.section);
  }

  function renderSection(id) {
    const d = state.digest;
    const sec = viewSections().find((s) => s.id === id) || d.sections[0];
    let html = "";
    let items;
    if (sec.id === "podcast") {
      // Las noticias del episodio, en el orden en que se cuentan, con su relevancia
      const ep = episodeFor(d.date);
      const rel = Object.fromEntries((ep.story_items || []).map((s) => [s.id, s.relevance]));
      items = visibleItems(sec.items, { allTopics: true }).map((it) => ({ ...it, _rel: rel[it.id] }));
    } else {
      items = visibleItems(sec.items);
    }
    if (sec.id === "top" && d.brief?.length) {
      html += `<section class="brief reveal" style="--i:0"><h2>Las claves del día</h2><ol>${d.brief
        .map((b) => `<li data-goto="${esc(b.ids?.[0] || "")}">${esc(b.text)}</li>`)
        .join("")}</ol><div class="ai-note">Resumen generado con IA a partir de titulares</div></section>`;
    }
    html += `<div class="section-head"><h2>${esc(sec.name)}</h2><span class="kicker">${items.length} noticias</span></div>`;
    html += items.length
      ? items.map((it, i) => storyHTML(it, i, sec.id === "top" && i === 0)).join("")
      : `<p class="empty">${state.prefs.hideRead ? "Lo has leído todo en esta sección." : "Hoy no hay noticias para este tema."}</p>`;
    $("#main").innerHTML = html;
  }

  /* ----------------------------------------------------------- "Todas": feed vertical */
  // Una noticia por pantalla; al deslizar encaja la siguiente entera (scroll-snap).
  function renderFeed(sec) {
    const d = state.digest;
    const topicName = Object.fromEntries(d.sections.map((s) => [s.id, s.short || s.name]));
    const items = visibleItems(sec.items);
    document.documentElement.classList.add("feed-open");
    const card = (it, i) => {
      const read = state.read.has(it.id);
      const img = state.prefs.showImages && it.image
        ? `<img class="card-img" src="${esc(safeUrl(it.image))}" alt="" loading="${i < 2 ? "eager" : "lazy"}" referrerpolicy="no-referrer" onerror="this.closest('.card').classList.remove('has-img');this.remove()">`
        : "";
      const text = it.ai_summary || it.summary || "";
      return `<article class="card${img ? " has-img" : ""}${read ? " read" : ""}" id="c-${esc(it.id)}">
        ${img}
        <div class="card-body">
          <div class="card-meta"><span class="src">${esc(it.source)}</span><span class="dot"></span><time datetime="${esc(it.published)}">${ago(it.published)}</time>${it.topics?.[0] ? `<span class="chip">${esc(topicName[it.topics[0]] || it.topics[0])}</span>` : ""}${read ? '<span class="chip">Leída ✓</span>' : ""}</div>
          <h2>${esc(it.title)}</h2>
          ${text ? `<p>${esc(text)}</p>` : ""}
          ${it.also?.length ? `<p class="card-also">También en ${it.also.map((a) => esc(a.source)).join(" · ")}</p>` : ""}
          <a class="card-open" href="${esc(safeUrl(it.url))}" target="_blank" rel="noopener" data-read="${esc(it.id)}">Leer en ${esc(it.source)} ›</a>
        </div>
      </article>`;
    };
    const layer = $("#feedLayer");
    layer.innerHTML = `<div class="feed" id="feed" tabindex="-1" data-edition="${esc(d.generated_at)}">
        <div class="feed-bar"><span class="feed-count" id="feedCount">1 / ${items.length}</span><button type="button" class="feed-close" id="feedClose" aria-label="Salir de Todas">✕</button></div>
        ${items.length ? items.map(card).join("") : `<article class="card"><div class="card-body"><h2>No hay noticias que mostrar.</h2></div></article>`}
        ${items.length ? `<article class="card card-end"><div class="card-body"><h2>Has llegado al final.</h2><p>${items.length} noticias de la edición de hoy.</p><button type="button" class="btn" id="feedTop">Volver arriba</button></div></article>` : ""}
      </div>`;
    const feed = $("#feed");
    feed.focus({ preventScroll: true });
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (!e.isIntersecting) continue;
        const n = [...feed.querySelectorAll(".card")].indexOf(e.target);
        $("#feedCount").textContent = `${Math.min(n + 1, items.length)} / ${items.length}`;
      }
    }, { root: feed, threshold: 0.6 });
    feed.querySelectorAll(".card").forEach((c) => io.observe(c));

    // Entrada: la pantalla completa se abre en círculo desde la pestaña pulsada
    const from = state.feedFrom;
    state.feedFrom = null;
    if (from && !reducedMotion()) {
      const R = Math.hypot(Math.max(from.x, innerWidth - from.x), Math.max(from.y, innerHeight - from.y));
      try {
        layer.animate([{ clipPath: `circle(0px at ${from.x}px ${from.y}px)` }, { clipPath: `circle(${R}px at ${from.x}px ${from.y}px)` }],
          { duration: 520, easing: "cubic-bezier(.3,.7,.1,1)" });
      } catch { layer.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 250 }); }
      feed.querySelector(".card-body")?.animate(
        [{ opacity: 0, transform: "translateY(26px)" }, { opacity: 1, transform: "none" }],
        { duration: 460, delay: 200, easing: "cubic-bezier(.2,.7,.2,1)", fill: "backwards" });
    }
  }

  const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

  function dropFeed() {
    if ($("#feedLayer").firstChild) $("#feedLayer").innerHTML = "";
    $("#feedLayer").style.transform = "";
    document.documentElement.classList.remove("feed-open");
  }

  function openFeed(tab) {
    const r = tab?.getBoundingClientRect();
    state.feedFrom = r ? { x: r.left + r.width / 2, y: r.top + r.height / 2 } : null;
    state.section = "todas";
    // una entrada en el historial: el gesto/botón "atrás" del móvil sale de Todas, no de la web
    if (!history.state?.feed) history.pushState({ feed: true }, "");
    renderTabs();
    renderPodcastBox();
    renderMain();
  }

  let closingFeed = false;
  let ignorePop = false;
  async function closeFeed({ fromPop = false } = {}) {
    if (closingFeed) return;
    closingFeed = true;
    const layer = $("#feedLayer");
    if ($("#feed") && !reducedMotion()) {
      // salida: la pantalla completa se va hacia la derecha y deja ver la Portada
      const start = layer.style.transform || "translateX(0px)";
      await layer.animate([{ transform: start }, { transform: "translateX(100%)" }],
        { duration: 280, easing: "cubic-bezier(.3,.6,.2,1)", fill: "forwards" }).finished.catch(() => {});
    }
    state.section = "top";
    store.set("section", "top");
    dropFeed();
    layer.getAnimations().forEach((a) => a.cancel());
    renderTabs();
    renderPodcastBox();
    renderSection("top");
    closingFeed = false;
    if (!fromPop && history.state?.feed) { ignorePop = true; history.back(); }
  }

  // Deslizar de izquierda a derecha: la pantalla sigue al dedo y, pasado un tercio
  // (o con un gesto rápido), sale de Todas. En vertical, el scroll normal.
  function bindFeedSwipe() {
    const layer = $("#feedLayer");
    let x0 = null, y0 = 0, t0 = 0, mode = null;
    const reset = () => { x0 = null; mode = null; const f = $("#feed"); if (f) f.style.overflowY = ""; };
    layer.addEventListener("touchstart", (e) => {
      if (e.touches.length !== 1 || closingFeed) return;
      x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; t0 = Date.now(); mode = null;
    }, { passive: true });
    layer.addEventListener("touchmove", (e) => {
      if (x0 === null) return;
      const dx = e.touches[0].clientX - x0, dy = e.touches[0].clientY - y0;
      if (!mode) {
        if (Math.abs(dx) < 10 && Math.abs(dy) < 10) return;
        mode = dx > 0 && Math.abs(dx) > Math.abs(dy) * 1.3 ? "x" : "y";
        if (mode === "x") $("#feed").style.overflowY = "hidden";
      }
      if (mode === "x") layer.style.transform = `translateX(${Math.max(0, dx)}px)`;
    }, { passive: true });
    layer.addEventListener("touchend", (e) => {
      if (mode === "x") {
        const dx = e.changedTouches[0].clientX - x0;
        const speed = dx / Math.max(1, Date.now() - t0);
        if (dx > innerWidth * 0.33 || (dx > 60 && speed > 0.5)) {
          reset();
          return closeFeed();
        }
        const back = layer.animate([{ transform: layer.style.transform }, { transform: "translateX(0px)" }],
          { duration: 220, easing: "cubic-bezier(.2,.7,.2,1)" });
        layer.style.transform = "";
        back.finished.catch(() => {});
      }
      reset();
    });
    layer.addEventListener("touchcancel", () => { layer.style.transform = ""; reset(); });
    window.addEventListener("popstate", () => {
      if (ignorePop) { ignorePop = false; return; }
      if (state.section === "todas") closeFeed({ fromPop: true });
    });
  }

  /* ----------------------------------------------------------- buscador */
  const fold = (s) => String(s || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();

  function searchPool() {
    // Todas las noticias del día (de todas las secciones) y, si se han cargado, las de días anteriores
    const d = state.digest;
    const topicName = Object.fromEntries(d.sections.map((s) => [s.id, s.name]));
    const pool = new Map();
    const add = (id, it, day) => {
      if (!it?.title || pool.has(id)) return;
      pool.set(id, { ...it, id, _day: day, _hay: fold([it.title, it.summary, it.ai_summary, it.source, ...(it.topics || []).map((t) => topicName[t] || t)].join(" ")) });
    };
    Object.entries(d.items).forEach(([id, it]) => add(id, it, null));
    (episodeFor(d.date)?.story_items || []).forEach((it) => add(it.id, it, null));
    if (state.archive) {
      Object.entries(state.archive)
        .sort((a, b) => b[0].localeCompare(a[0]))
        .forEach(([day, dg]) => { if (day !== d.date) Object.entries(dg.items).forEach(([id, it]) => add(id, it, day)); });
    }
    return [...pool.values()];
  }

  function renderSearch() {
    const terms = fold(state.query).split(/\s+/).filter(Boolean);
    const hits = searchPool()
      .filter((it) => terms.every((t) => it._hay.includes(t)))
      .sort((a, b) => (a._day ? 1 : 0) - (b._day ? 1 : 0) || (b._day || "").localeCompare(a._day || "") || (b.score || 0) - (a.score || 0));
    const others = state.days.filter((day) => day !== state.digest.date).length;
    let html = `<div class="section-head"><h2>«${esc(state.query)}»</h2><span class="kicker">${hits.length} ${hits.length === 1 ? "noticia" : "noticias"}</span></div>`;
    html += hits.length
      ? hits.slice(0, 80).map((it, i) => storyHTML(it, i, false)).join("")
      : `<p class="empty">Ninguna noticia ${state.archive ? "de estos días" : "de hoy"} contiene esas palabras.</p>`;
    if (!state.archive && others) {
      html += `<button type="button" class="btn more-btn" id="searchArchive">Buscar también en los ${others} días anteriores</button>`;
    }
    $("#main").innerHTML = html;
  }

  async function loadArchive() {
    const b = $("#searchArchive");
    if (b) { b.disabled = true; b.textContent = "Cargando días anteriores…"; }
    const archive = {};
    await Promise.all(state.days.map(async (day) => {
      try { archive[day] = await getJSON(`data/${day}.json`); } catch {}
    }));
    state.archive = archive;
    renderMain();
  }

  function renderFooter() {
    const d = state.digest;
    const cls = (s) => (!s.ok ? "ko" : s.via === "rss" ? "ok" : "gn");
    $("#colophon").innerHTML = `Medios consultados · ${d.stats.fetched} titulares, ${d.stats.stories} historias${d.ai_model ? " · resúmenes: " + esc(d.ai_model) : ""}
      <ul>${d.sources.map((s) => `<li class="${cls(s)}" title="${esc((s.errors || []).join("\n"))}">${esc(s.name)} · ${s.count}${s.via !== "rss" ? " (G. News)" : ""}</li>`).join("")}</ul>`;
  }

  function renderSettings() {
    const d = state.digest;
    $(`input[name=theme][value=${state.prefs.theme}]`).checked = true;
    $(`input[name=size][value=${state.prefs.size}]`).checked = true;
    $("#hideRead").checked = state.prefs.hideRead;
    $("#showImages").checked = state.prefs.showImages;
    renderPodcastSettings();
    $("#topicToggles").innerHTML = d.sections
      .filter((s) => s.id !== "top")
      .map((s) => `<label class="switch"><input type="checkbox" data-topic="${esc(s.id)}" ${state.hiddenTopics.has(s.id) ? "" : "checked"}><span>${esc(s.name)}</span></label>`)
      .join("");
    $("#sourceToggles").innerHTML = d.sources
      .map((s) => `<label class="switch"><input type="checkbox" data-src="${esc(s.id)}" ${state.hiddenSources.has(s.id) ? "" : "checked"}><span>${esc(s.name)}</span></label>`)
      .join("");
  }

  /* ----------------------------------------------------------- podcast: temas en el repo */
  // La app guarda la elección en config/podcast.yaml del repositorio con un token
  // de GitHub (solo este repo, Contents: read/write) que vive en este dispositivo.
  const gh = {
    repo: () => state.podcast?.repo || `${location.hostname.split(".")[0]}/${location.pathname.split("/")[1]}`,
    token: () => store.get("ghToken", ""),
    async req(path, opts = {}) {
      const r = await fetch(`https://api.github.com/repos/${gh.repo()}${path}`, {
        ...opts,
        headers: { Accept: "application/vnd.github+json", Authorization: `Bearer ${gh.token()}`, "X-GitHub-Api-Version": "2022-11-28", ...(opts.headers || {}) },
      });
      if (!r.ok) throw new Error(`GitHub ${r.status}`);
      return r.json();
    },
  };
  const b64enc = (str) => { let bin = ""; new TextEncoder().encode(str).forEach((b) => (bin += String.fromCharCode(b))); return btoa(bin); };
  const b64dec = (b64) => new TextDecoder().decode(Uint8Array.from(atob(b64.replace(/\s/g, "")), (c) => c.charCodeAt(0)));
  const TOPICS_RE = /^topics:\s*\[([^\]]*)\].*$/m;
  const pod = { file: null, chosen: null };

  async function loadPodcastConfig() {
    const f = await gh.req("/contents/config/podcast.yaml");
    const text = b64dec(f.content);
    const m = text.match(TOPICS_RE);
    pod.file = { sha: f.sha, text };
    pod.chosen = new Set(m ? m[1].split(",").map((x) => x.trim()).filter(Boolean) : []);
  }

  function renderPodcastSettings() {
    const secs = state.digest.sections.filter((s) => s.id !== "top");
    const chosen = pod.chosen || new Set(state.podcast?.topics_next || []);
    const connected = !!gh.token();
    $("#podcastTopics").innerHTML = secs
      .map((s) => `<label class="switch"><input type="checkbox" data-ptopic="${esc(s.id)}" ${chosen.has(s.id) ? "checked" : ""} ${connected ? "" : "disabled"}><span>${esc(s.short || s.name)}</span></label>`)
      .join("");
    $("#ghBox").innerHTML = connected
      ? `<div class="gh-row"><button type="button" class="btn" id="podSave">Guardar temas del podcast</button><button type="button" class="link-btn" id="ghOut">Desconectar GitHub</button></div>`
      : `<p class="hint">Para cambiar los temas desde aquí, conecta GitHub una vez en este dispositivo con tu token (solo este repo, permiso Contents: Read and write).</p>
         <div class="gh-row"><input type="password" id="ghToken" placeholder="github_pat_…" autocomplete="off"><button type="button" class="btn" id="ghIn">Conectar</button></div>`;
    $("#feedHint").innerHTML = state.podcast?.feed
      ? `Suscríbete en tu app de podcasts con esta URL: <code>${esc(state.podcast.feed)}</code> <button type="button" class="link-btn" id="feedCopy">Copiar</button>`
      : "";
    updatePodSave();
    if (connected && !pod.file) {
      loadPodcastConfig().then(renderPodcastSettings).catch((e) => toast("No se pudo leer la configuración: " + e.message));
    }
  }

  function podChecked() { return [...document.querySelectorAll("[data-ptopic]:checked")].map((x) => x.dataset.ptopic); }
  function updatePodSave() {
    const b = $("#podSave");
    if (!b) return;
    const n = podChecked().length;
    b.disabled = n < 3 || n > 5 || !pod.file;
    b.textContent = n < 3 ? `Elige ${3 - n} más` : "Guardar temas del podcast";
  }

  async function podcastSettingsClick(e) {
    const id = e.target.id;
    if (id === "ghIn") {
      const tok = $("#ghToken").value.trim();
      if (!tok) return;
      store.set("ghToken", tok);
      try {
        const r = await gh.req("");
        if (!r.permissions?.push) throw new Error("el token no tiene permiso de escritura");
        await loadPodcastConfig();
        toast("GitHub conectado");
      } catch (err) {
        store.set("ghToken", "");
        toast("No se pudo conectar: " + err.message);
      }
      renderPodcastSettings();
    } else if (id === "ghOut") {
      store.set("ghToken", "");
      pod.file = pod.chosen = null;
      renderPodcastSettings();
      toast("GitHub desconectado de este dispositivo");
    } else if (id === "podSave") {
      const topics = podChecked();
      const b = e.target;
      b.disabled = true; b.textContent = "Guardando…";
      try {
        const text = pod.file.text.replace(TOPICS_RE, `topics: [${topics.join(", ")}]`);
        const names = topics.map((t) => state.digest.sections.find((s) => s.id === t)?.short || t);
        const r = await gh.req("/contents/config/podcast.yaml", {
          method: "PUT",
          body: JSON.stringify({ message: `Podcast: ${names.join(", ")}`, content: b64enc(text), sha: pod.file.sha }),
        });
        pod.file = { sha: r.content.sha, text };
        pod.chosen = new Set(topics);
        toast("Guardado: el podcast de mañana tratará " + names.join(", "));
      } catch (err) {
        toast("No se pudo guardar: " + err.message);
        pod.file = null; // relee por si cambió en GitHub
      }
      renderPodcastSettings();
    } else if (id === "feedCopy") {
      navigator.clipboard?.writeText(state.podcast.feed).then(() => toast("URL copiada"), () => toast(state.podcast.feed));
    }
  }

  function render() {
    renderHeader();
    renderTabs();
    renderPodcastBox();
    renderMain();
    renderFooter();
  }

  /* ----------------------------------------------------------- eventos */
  function markRead(id) {
    if (!id || state.read.has(id)) return;
    state.read.add(id);
    // guarda solo los últimos 600 ids para no crecer sin fin
    store.set("read", [...state.read].slice(-600));
    setTimeout(() => {
      renderTabs();
      if (state.section === "todas" && !state.query) {
        $(`#c-${CSS.escape(id)}`)?.classList.add("read");
        renderSection("top");
      } else {
        renderMain();
      }
    }, 400);
  }

  function setQuery(v) {
    const query = v.trim();
    $("#qClear").hidden = !v;
    if (!v) $("#q").value = "";
    if (query === state.query) return;
    state.query = query;
    if (query) document.documentElement.classList.remove("feed-open");
    renderTabs();
    renderPodcastBox();
    renderMain();
  }

  function feedKeys(e) {
    if (state.section !== "todas" || state.query || $("#settings").open) return;
    const feed = $("#feed");
    if (!feed) return;
    if (e.key === "Escape") return closeFeed();
    const step = { ArrowDown: 1, PageDown: 1, " ": 1, ArrowUp: -1, PageUp: -1 }[e.key];
    if (step && e.target.tagName !== "INPUT") {
      e.preventDefault();
      feed.scrollBy({ top: step * feed.clientHeight, behavior: "smooth" });
    }
  }

  function bind() {
    document.addEventListener("keydown", feedKeys);
    bindFeedSwipe();
    $("#feedLayer").addEventListener("click", (e) => {
      if (e.target.id === "feedClose") return closeFeed();
      if (e.target.id === "feedTop") return $("#feed").scrollTo({ top: 0, behavior: "smooth" });
      const a = e.target.closest("[data-read]");
      if (a) markRead(a.dataset.read);
    });
    $("#tabs").addEventListener("click", (e) => {
      const b = e.target.closest(".tab");
      if (!b) return;
      if (b.dataset.id === "todas") return openFeed(b);
      state.section = b.dataset.id;
      store.set("section", state.section);
      setQuery("");
      renderTabs();
      renderPodcastBox();
      renderMain();
      if (state.section !== "todas") window.scrollTo({ top: $(".tabs").offsetTop, behavior: "smooth" });
    });

    // Buscador: filtra todas las noticias mientras se escribe
    const q = $("#q");
    let qTimer;
    q.addEventListener("input", () => {
      clearTimeout(qTimer);
      qTimer = setTimeout(() => setQuery(q.value), 150);
    });
    q.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { q.value = ""; setQuery(""); q.blur(); }
      if (e.key === "Enter") q.blur(); // cierra el teclado en el móvil
    });
    $("#qClear").addEventListener("click", () => { q.value = ""; setQuery(""); q.focus(); });

    $("#main").addEventListener("click", (e) => {
      if (e.target.id === "searchArchive") return loadArchive();
      const a = e.target.closest("[data-read]");
      if (a) return markRead(a.dataset.read);
      const li = e.target.closest("[data-goto]");
      if (li?.dataset.goto) {
        const el = document.getElementById("s-" + li.dataset.goto);
        if (el) { el.scrollIntoView({ behavior: "smooth", block: "center" }); el.animate([{ background: "var(--accent-soft)" }, { background: "transparent" }], { duration: 1400 }); }
      }
    });

    $("#prevDay").addEventListener("click", () => {
      loadDay(state.dayIdx + 1).then(() => window.scrollTo({ top: 0 }));
    });
    $("#edition").addEventListener("click", (e) => {
      if (e.target.id === "goToday") loadDay(0).then(() => window.scrollTo({ top: 0 }));
    });

    const dlg = $("#settings");
    $("#openSettings").addEventListener("click", () => {
      try { renderSettings(); } catch (err) { showError(err); }
      try {
        if (dlg.open) dlg.close();   // si se quedó abierto "a medias", se reabre limpio
        dlg.showModal();
      } catch (err) {
        dlg.setAttribute("open", ""); // último recurso: abrir sin modo modal
        showError(err);
      }
    });
    dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
    dlg.addEventListener("click", (e) => { if (e.target.closest("#ghBox, #feedHint")) podcastSettingsClick(e); });
    dlg.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.id === "ghToken") { e.preventDefault(); $("#ghIn").click(); } });
    dlg.addEventListener("change", (e) => {
      const t = e.target;
      if (t.dataset.ptopic) {
        if (podChecked().length > 5) { t.checked = false; toast("Como mucho 5 temas"); }
        return updatePodSave();
      }
      if (t.name === "theme") state.prefs.theme = t.value;
      else if (t.name === "size") state.prefs.size = t.value;
      else if (t.id === "hideRead") state.prefs.hideRead = t.checked;
      else if (t.id === "showImages") state.prefs.showImages = t.checked;
      else if (t.dataset.topic) {
        t.checked ? state.hiddenTopics.delete(t.dataset.topic) : state.hiddenTopics.add(t.dataset.topic);
        store.set("hiddenTopics", [...state.hiddenTopics]);
      } else if (t.dataset.src) {
        t.checked ? state.hiddenSources.delete(t.dataset.src) : state.hiddenSources.add(t.dataset.src);
        store.set("hiddenSources", [...state.hiddenSources]);
      }
      applyPrefs();
      render();
    });
    $("#clearRead").addEventListener("click", () => {
      state.read.clear(); store.set("read", []); render(); toast("Todo marcado como no leído");
    });

    // Al volver a la app (p. ej. desde el navegador del periódico) refresca si hay edición nueva
    document.addEventListener("visibilitychange", async () => {
      if (document.visibilityState !== "visible" || state.dayIdx !== 0) return;
      try {
        const fresh = await getJSON("data/latest.json");
        if (fresh.generated_at !== state.digest?.generated_at) {
          state.digest = fresh; await loadIndex(); render(); toast("Nueva edición disponible");
        }
      } catch {}
    });
  }

  /* ----------------------------------------------------------- arranque */
  async function init() {
    applyPrefs();
    bind();
    await loadIndex();
    await loadDay(0);
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
  }
  init();
})();
