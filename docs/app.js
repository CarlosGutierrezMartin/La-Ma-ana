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

    const ep = sec.id === "top" && state.podcast?.episodes?.find((e) => e.date === d.date);
    if (ep) {
      const mins = Math.max(1, Math.round(ep.duration / 60));
      html += `<section class="podcast reveal" style="--i:0">
        <span class="kicker">Podcast · ${mins} min</span>
        <h2>${esc(ep.title)}</h2>
        <audio controls preload="none" src="podcast/${esc(ep.file)}"></audio>
        <p class="pod-topics">${esc((ep.topic_names || []).join(" · "))}</p>
        ${ep.transcript?.length ? `<details><summary>Leer el guion</summary>${ep.transcript.map((t) => `<p><b>${esc(t.speaker)}</b> ${esc(t.text)}</p>`).join("")}</details>` : ""}
      </section>`;
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
    const old = $("#main .podcast audio");
    const keep = old && !old.paused ? old : null; // si está sonando, no se toca
    $("#main").innerHTML = html;
    if (keep) {
      const fresh = $("#main .podcast audio");
      if (fresh && fresh.getAttribute("src") === keep.getAttribute("src")) fresh.replaceWith(keep);
    }
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
