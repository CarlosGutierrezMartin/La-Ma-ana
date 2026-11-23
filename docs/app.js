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
    section: store.get("section", "top"),
    read: new Set(store.get("read", [])),
    hiddenSources: new Set(store.get("hiddenSources", [])),
    hiddenTopics: new Set(store.get("hiddenTopics", [])),
    prefs: Object.assign({ theme: "auto", size: "m", hideRead: false, showImages: true }, store.get("prefs", {})),
  };

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");
  const tz = "Europe/Madrid";
  const fmtTime = (iso) => new Date(iso).toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit", timeZone: tz });
  const fmtDay = (ymd) => new Date(ymd + "T12:00:00").toLocaleDateString("es-ES", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  function ago(iso) {
    const h = (Date.now() - new Date(iso)) / 36e5;
    if (h < 1) return `hace ${Math.max(1, Math.round(h * 60))} min`;
    if (h < 24) return `hace ${Math.round(h)} h`;
    return fmtTime(iso);
  }
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
  function visibleItems(ids) {
    const items = state.digest.items;
    return ids
      .map((id) => ({ id, ...items[id] }))
      .filter((it) => it.title && !state.hiddenSources.has(it.source_id))
      // fuera si todos sus temas están ocultos (las que no tienen tema se quedan)
      .filter((it) => !(it.topics?.length && it.topics.every((t) => state.hiddenTopics.has(t))))
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
    const secs = state.digest.sections.filter((s) => s.items.length && !state.hiddenTopics.has(s.id));
    if (!secs.find((s) => s.id === state.section)) state.section = secs[0]?.id || "top";
    $("#tabs").innerHTML = secs
      .map((s) => {
        const unread = s.items.filter((id) => !state.read.has(id)).length;
        return `<button class="tab" role="tab" data-id="${esc(s.id)}" aria-selected="${s.id === state.section}">${esc(s.short || s.name)}<span class="count">${unread}</span></button>`;
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
      <div class="meta"><span class="num">${String(i + 1).padStart(2, "0")}</span><span class="src">${esc(it.source)}</span><span class="dot"></span><time datetime="${esc(it.published)}">${ago(it.published)}</time>${read ? '<span class="read-mark">Leída ✓</span>' : ""}</div>
      ${lead ? img : ""}
      <h3>${esc(it.title)}</h3>
      ${lead ? "" : img}
      ${text}
      ${also}
    </article>`;
  }

  function renderMain() {
    const d = state.digest;
    const sec = d.sections.find((s) => s.id === state.section) || d.sections[0];
    const items = visibleItems(sec.items);
    let html = "";

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
    $("#topicToggles").innerHTML = d.sections
      .filter((s) => s.id !== "top")
      .map((s) => `<label class="switch"><input type="checkbox" data-topic="${esc(s.id)}" ${state.hiddenTopics.has(s.id) ? "" : "checked"}><span>${esc(s.name)}</span></label>`)
      .join("");
    $("#sourceToggles").innerHTML = d.sources
      .map((s) => `<label class="switch"><input type="checkbox" data-src="${esc(s.id)}" ${state.hiddenSources.has(s.id) ? "" : "checked"}><span>${esc(s.name)}</span></label>`)
      .join("");
  }

  function render() {
    renderHeader();
    renderTabs();
    renderMain();
    renderFooter();
  }

  /* ----------------------------------------------------------- eventos */
  function markRead(id) {
    if (!id || state.read.has(id)) return;
    state.read.add(id);
    // guarda solo los últimos 600 ids para no crecer sin fin
    store.set("read", [...state.read].slice(-600));
    setTimeout(() => { renderTabs(); renderMain(); }, 400);
  }

  function bind() {
    $("#tabs").addEventListener("click", (e) => {
      const b = e.target.closest(".tab");
      if (!b) return;
      state.section = b.dataset.id;
      store.set("section", state.section);
      renderTabs();
      renderMain();
      window.scrollTo({ top: $(".tabs").offsetTop, behavior: "smooth" });
    });

    $("#main").addEventListener("click", (e) => {
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
    $("#openSettings").addEventListener("click", () => { renderSettings(); dlg.showModal(); });
    dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
    dlg.addEventListener("change", (e) => {
      const t = e.target;
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
