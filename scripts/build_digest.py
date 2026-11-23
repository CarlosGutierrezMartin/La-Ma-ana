#!/usr/bin/env python3
"""Genera el digest diario de noticias para la PWA.

Flujo:
  1. Descarga en paralelo los RSS de config/sources.yaml (con fallback a
     Google News por dominio si los feeds de una fuente fallan).
  2. Normaliza, filtra por ventana temporal y exclusiones.
  3. Agrupa la misma noticia publicada por varios medios (cobertura cruzada
     = señal de relevancia).
  4. Puntúa por temas (config/topics.yaml), cobertura, portada y recencia.
  5. Opcional: resumen del día + resumen por noticia con Claude.
  6. Escribe docs/data/<fecha>.json, latest.json e index.json.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import html
import http.cookiejar
import json
import math
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from calendar import timegm
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import yaml

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Europe/Madrid")

STOPWORDS = set(
    """
    el la los las un una unos unas de del al a en y o u que por para con sin se su sus
    es son ha han lo le les mas más pero como este esta estos estas ese esa eso ya no si
    sobre entre tras hasta desde cuando donde muy tambien también fue ser era hay sera será
    the a an of to in on for and or with at by from is are was were be has have its it as
    that this after over new says said will can into about than more how why what who
    """.split()
)


# --------------------------------------------------------------------------- utils
def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm(s: str) -> str:
    return strip_accents(s).lower()


def clean_html(s: str, limit: int = 420) -> str:
    # Algunos feeds (p. ej. Optometry Times) dejan el CDATA sin cerrar dentro del
    # titular; sin esto, el quitador de etiquetas se come el titular entero.
    s = re.sub(r"<!\[CDATA\[(.*?)(?:\]\]>|\]?$)", r"\1", s or "", flags=re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0] + "…"
    return s


def item_id(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:10]


def gnews_url(query: str, lang: str) -> str:
    q = urllib.parse.quote(f"{query} when:1d")
    if lang == "en":
        return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    return f"https://news.google.com/rss/search?q={q}&hl=es&gl=ES&ceid=ES:es"


class KeywordMatcher:
    """Palabra completa, sin tildes. Si la keyword tiene mayúsculas, las respeta."""

    def __init__(self, keywords: list[str]):
        self.patterns: list[tuple[re.Pattern, bool]] = []
        for kw in keywords or []:
            kw = str(kw).strip()
            if not kw:
                continue
            prefix = kw.endswith("*")
            core = kw.rstrip("*")
            case_sensitive = any(c.isupper() for c in core)
            base = strip_accents(core) if case_sensitive else norm(core)
            pat = r"(?<!\w)" + re.escape(base).replace(r"\ ", r"\s+") + (r"\w*" if prefix else r"(?!\w)")
            self.patterns.append((re.compile(pat), case_sensitive))

    def count(self, text: str) -> int:
        if not text:
            return 0
        plain, lower = strip_accents(text), norm(text)
        return sum(1 for p, cs in self.patterns if p.search(plain if cs else lower))


# --------------------------------------------------------------------------- data
@dataclass
class Article:
    url: str
    title: str
    summary: str
    source_id: str
    source_name: str
    published: datetime
    image: str | None = None
    frontpage: bool = False
    weight: float = 1.0
    id: str = ""
    topics: dict[str, float] = field(default_factory=dict)
    score: float = 0.0
    also: list[dict] = field(default_factory=list)
    tokens: set[str] = field(default_factory=set)

    def __post_init__(self):
        self.id = item_id(self.url)
        words = re.findall(r"\w+", norm(self.title))
        # "raíz" barata: 5 primeras letras (alemana/alemania, compra/compras...)
        self.tokens = {w[:5] for w in words if len(w) > 2 and w not in STOPWORDS}


# --------------------------------------------------------------------------- fetch
def http_get(url: str, ua: str, timeout: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "application/rss+xml, application/xml, text/xml, */*"})
    # Con cookies: algunos medios (p. ej. El Confidencial) ponen un anti-bot que
    # redirige hasta que el cliente devuelve la cookie; sin ella, bucle infinito.
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    with opener.open(req, timeout=timeout) as r:
        return r.read()


def pick_image(e) -> str | None:
    for key in ("media_content", "media_thumbnail"):
        for m in e.get(key, []) or []:
            u = m.get("url")
            if u and not u.lower().endswith((".mp4", ".mp3")):
                return u
    for l in e.get("links", []) or []:
        if l.get("rel") == "enclosure" and str(l.get("type", "")).startswith("image"):
            return l.get("href")
    m = re.search(r'<img[^>]+src="([^"]+)"', e.get("summary", "") or "")
    return m.group(1) if m else None


def parse_feed(raw: bytes, src: dict, frontpage: bool, via_gnews: bool) -> list[Article]:
    parsed = feedparser.parse(raw)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"feed no válido: {parsed.bozo_exception}")
    out = []
    for e in parsed.entries:
        url, title = e.get("link"), clean_html(e.get("title", ""), 300)
        # "categoría | Titular" (ConSalud y otros): quitar la categoría
        title = re.sub(r"^[^|]{2,30}\s\|\s+(?=\S.{15,})", "", title)
        if not url or not title:
            continue
        t = e.get("published_parsed") or e.get("updated_parsed")
        pub = datetime.fromtimestamp(timegm(t), tz=timezone.utc) if t else datetime.now(timezone.utc)
        summary = clean_html(e.get("summary", "") or e.get("description", ""))
        name = src["name"]
        if via_gnews:
            # Google News añade " - Medio" al titular y su entradilla repite el titular
            title = re.sub(r"\s+-\s+[^-]{2,60}$", "", title)
            summary = ""
            if src.get("search"):
                # Fuente temática (búsqueda): mostrar el medio real, no "Google News"
                name = (e.get("source") or {}).get("title") or name
        if summary and norm(summary).startswith(norm(title)[:60]):
            summary = summary[len(title):].strip(" .-–") if len(summary) > len(title) + 20 else ""
        out.append(
            Article(
                url=url, title=title, summary=summary, source_id=src["id"], source_name=name,
                published=pub, image=None if via_gnews else pick_image(e), frontpage=frontpage,
                weight=float(src.get("weight", 1.0)),
            )
        )
    return out


def fetch_source(src: dict, settings: dict) -> tuple[list[Article], dict]:
    ua, timeout = settings.get("user_agent", "MiDiario/1.0"), int(settings.get("timeout_seconds", 15))
    arts, errors = [], []
    for f in src.get("feeds") or []:
        try:
            arts += parse_feed(http_get(f["url"], ua, timeout), src, bool(f.get("frontpage")), False)
        except Exception as ex:  # noqa: BLE001
            errors.append(f"{f['url']}: {type(ex).__name__}: {ex}"[:200])
    via = "rss"
    # También si los feeds responden pero vacíos (p. ej. elEconomista devuelve un canal sin noticias)
    query = src.get("search") or src.get("fallback_gnews")
    if not arts and query:
        via = "google-news"
        try:
            url = gnews_url(query, src.get("lang", "es"))
            arts += parse_feed(http_get(url, ua, timeout), src, False, True)
        except Exception as ex:  # noqa: BLE001
            errors.append(f"google-news: {type(ex).__name__}: {ex}"[:200])
    status = {"id": src["id"], "name": src["name"], "via": via, "count": len(arts), "ok": len(arts) > 0, "errors": errors}
    return arts, status


# --------------------------------------------------------------------------- rank
def cluster(articles: list[Article], threshold: float = 0.5) -> list[Article]:
    """Agrupa titulares casi iguales de distintos medios. Devuelve representantes."""
    articles = sorted(articles, key=lambda a: (a.frontpage, len(a.summary), a.weight), reverse=True)
    reps: list[Article] = []
    for a in articles:
        best, best_sim = None, 0.0
        for r in reps:
            if not a.tokens or not r.tokens:
                continue
            inter = len(a.tokens & r.tokens)
            sim = inter / min(len(a.tokens), len(r.tokens))  # coeficiente de solapamiento
            if inter >= 3 and sim > best_sim:
                best, best_sim = r, sim
        if best and best_sim >= threshold:
            if a.source_id != best.source_id and all(x["source_id"] != a.source_id for x in best.also):
                best.also.append({"source_id": a.source_id, "source": a.source_name, "url": a.url, "title": a.title})
            best.frontpage = best.frontpage or a.frontpage
            if not best.image and a.image:
                best.image = a.image
        else:
            reps.append(a)
    return reps


def score_articles(reps: list[Article], topics: list[dict], now: datetime, source_topics: dict | None = None) -> None:
    """source_topics: {source_id: [topic_id, ...]} para medios especializados; sus
    noticias cuentan para ese tema aunque no usen ninguna palabra clave."""
    matchers = {t["id"]: KeywordMatcher(t.get("keywords", [])) for t in topics}
    source_topics = source_topics or {}
    for a in reps:
        for t in topics:
            m = matchers[t["id"]]
            raw = 3 * m.count(a.title) + m.count(a.summary)
            if t["id"] in source_topics.get(a.source_id, ()):
                raw += int(t.get("min_score", 2))
            if raw >= int(t.get("min_score", 2)):
                a.topics[t["id"]] = raw * float(t.get("weight", 1.0))
        topic_score = max(a.topics.values(), default=0.0) + 0.25 * (sum(a.topics.values()) - max(a.topics.values(), default=0.0))
        coverage = 4.0 * len(a.also)
        front = 2.0 if a.frontpage else 0.0
        hours = max(0.0, (now - a.published).total_seconds() / 3600)
        recency = 0.5 + 0.5 * math.exp(-hours / 18)
        a.score = round((topic_score + coverage + front) * a.weight * recency, 3)


def select(reps: list[Article], topics: list[dict], per_source_cap: int = 4) -> tuple[list[dict], list[Article]]:
    by_score = sorted(reps, key=lambda a: a.score, reverse=True)

    def take(pool, n, src_cap=per_source_cap, topic_cap=None):
        picked, per_src, per_topic = [], {}, {}
        for a in pool:
            main = max(a.topics, key=a.topics.get) if a.topics else None
            if per_src.get(a.source_id, 0) >= src_cap:
                continue
            if topic_cap and main and per_topic.get(main, 0) >= topic_cap:
                continue
            picked.append(a)
            per_src[a.source_id] = per_src.get(a.source_id, 0) + 1
            if main:
                per_topic[main] = per_topic.get(main, 0) + 1
            if len(picked) >= n:
                break
        return picked

    sections = []
    # Portada variada: como mucho 2 noticias por medio y 3 por tema principal
    top = take([a for a in by_score if a.score > 0], 15, src_cap=2, topic_cap=3)
    sections.append({"id": "top", "name": "Lo más relevante", "short": "Portada", "items": [a.id for a in top]})
    used = {a.id for a in top}
    for t in topics:
        pool = sorted((a for a in reps if t["id"] in a.topics), key=lambda a: a.score + a.topics[t["id"]], reverse=True)
        items = take(pool, int(t.get("max_items", 12)))
        used |= {a.id for a in items}
        sections.append({"id": t["id"], "name": t["name"], "short": t.get("short", t["name"]), "items": [a.id for a in items]})
    return sections, [a for a in by_score if a.id in used]


# --------------------------------------------------------------------------- AI
def ai_enrich(selected: list[Article], ai_cfg: dict, topic_names: dict) -> dict | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not ai_cfg.get("enabled", True) or not selected:
        return None
    try:
        import anthropic
    except ImportError:
        return None
    cands = selected[: int(ai_cfg.get("candidates", 40))]
    lines = []
    for a in cands:
        also = ", ".join(x["source"] for x in a.also)
        lines.append(
            f"[{a.id}] ({a.source_name}{'; también en ' + also if also else ''}) {a.title}"
            + (f" — {a.summary[:300]}" if a.summary else "")
        )
    n = int(ai_cfg.get("brief_points", 5))
    prompt = f"""Eres el editor de un diario personal. Los temas del lector, por orden de interés: {', '.join(topic_names.values())}.

Noticias de hoy (id entre corchetes):
{chr(10).join(lines)}

Devuelve SOLO un JSON con esta forma:
{{"brief": [{{"text": "...", "ids": ["id1", "id2"]}}],
  "summaries": {{"id": "..."}}}}

Reglas:
- "brief": los {n} hechos más importantes del día para este lector, una frase cada uno (máx. 30 palabras), en español, con los ids que lo respaldan.
- "summaries": para cada noticia, 1-2 frases en español que digan qué ha pasado y por qué importa. Usa solo la información del titular y la entradilla; si no basta, resume el titular sin inventar datos.
- Estilo sobrio y directo. Sin guiones largos ni fórmulas como "no es X, sino Y".
"""
    try:
        client = anthropic.Anthropic(api_key=key)
        msg = client.messages.create(
            model=ai_cfg.get("model", "claude-haiku-5-5"),
            # Haiku 5.5 razona por defecto y ese razonamiento cuenta en max_tokens:
            # margen amplio para que el JSON de 40 resúmenes no se corte, y esfuerzo
            # bajo porque resumir titulares no necesita más (y abarata la llamada).
            max_tokens=16000,
            output_config={"effort": ai_cfg.get("effort", "low")},
            messages=[{"role": "user", "content": prompt}],
        )
        if msg.stop_reason != "end_turn":
            raise RuntimeError(f"respuesta incompleta (stop_reason={msg.stop_reason})")
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        data = json.loads(text[text.index("{"): text.rindex("}") + 1])
        valid = {a.id for a in cands}
        brief = [
            {"text": str(b["text"]), "ids": [i for i in b.get("ids", []) if i in valid]}
            for b in data.get("brief", [])
            if b.get("text")
        ][:n]
        summaries = {k: str(v) for k, v in (data.get("summaries") or {}).items() if k in valid}
        return {"brief": brief, "summaries": summaries, "model": ai_cfg.get("model")}
    except Exception as ex:  # noqa: BLE001
        print(f"[ai] sin resumen: {type(ex).__name__}: {ex}", file=sys.stderr)
        return None


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default=ROOT / "config/sources.yaml", type=Path)
    ap.add_argument("--topics", default=ROOT / "config/topics.yaml", type=Path)
    ap.add_argument("--out", default=ROOT / "docs/data", type=Path)
    ap.add_argument("--now", help="ISO datetime (para tests)")
    ap.add_argument("--retention-days", type=int, default=14)
    args = ap.parse_args()

    scfg = yaml.safe_load(args.sources.read_text(encoding="utf-8"))
    tcfg = yaml.safe_load(args.topics.read_text(encoding="utf-8"))
    settings = scfg.get("settings", {})
    sources = [s for s in scfg["sources"] if s.get("enabled", True)]
    topics = tcfg["topics"]
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    # 1. fetch
    articles, statuses = [], []
    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        for arts, st in pool.map(lambda s: fetch_source(s, settings), sources):
            articles += arts
            statuses.append(st)
            flag = "OK " if st["ok"] else "ERR"
            print(f"[{flag}] {st['name']:<24} {st['count']:>3} noticias via {st['via']}" + (f"  ({'; '.join(st['errors'])})" if st["errors"] else ""))

    # 2. filter
    window = timedelta(hours=float(settings.get("window_hours", 30)))
    windows = {s["id"]: timedelta(hours=float(s["window_hours"])) for s in sources if s.get("window_hours")}
    excl = KeywordMatcher(tcfg.get("exclude", []))
    seen, fresh = set(), []
    for a in articles:
        key = a.url.split("?")[0].rstrip("/")
        if key in seen or now - a.published > windows.get(a.source_id, window) or a.published - now > timedelta(hours=2) or excl.count(a.title):
            continue
        seen.add(key)
        fresh.append(a)

    # 3-4. cluster + score + select
    reps = cluster(fresh)
    score_articles(reps, topics, now, {s["id"]: s.get("topics", []) for s in sources})
    sections, selected = select(reps, topics)

    # 5. AI
    ai = ai_enrich(selected, tcfg.get("ai", {}), {t["id"]: t["name"] for t in topics})

    # 6. write
    local = now.astimezone(TZ)
    day = local.date().isoformat()
    items = {
        a.id: {
            "title": a.title,
            "url": a.url,
            "source": a.source_name,
            "source_id": a.source_id,
            "published": a.published.isoformat(),
            "summary": a.summary,
            "ai_summary": (ai or {}).get("summaries", {}).get(a.id),
            "image": a.image,
            "topics": sorted(a.topics, key=a.topics.get, reverse=True),
            "score": a.score,
            "also": a.also,
        }
        for a in selected
    }
    digest = {
        "date": day,
        "generated_at": now.isoformat(),
        "stats": {"fetched": len(articles), "fresh": len(fresh), "stories": len(reps), "selected": len(selected)},
        "brief": (ai or {}).get("brief"),
        "ai_model": (ai or {}).get("model"),
        "sections": sections,
        "items": items,
        "sources": statuses,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(digest, ensure_ascii=False, indent=1)
    (args.out / f"{day}.json").write_text(payload, encoding="utf-8")
    (args.out / "latest.json").write_text(payload, encoding="utf-8")

    # índice de días + limpieza
    cutoff = (local.date() - timedelta(days=args.retention_days)).isoformat()
    days = []
    for p in sorted(args.out.glob("????-??-??.json"), reverse=True):
        if p.stem < cutoff:
            p.unlink()
        else:
            days.append(p.stem)
    (args.out / "index.json").write_text(json.dumps({"days": days, "updated": now.isoformat()}), encoding="utf-8")

    ok = sum(s["ok"] for s in statuses)
    print(f"\n{day}: {len(articles)} descargadas, {len(fresh)} recientes, {len(reps)} historias, {len(selected)} seleccionadas. Fuentes OK {ok}/{len(statuses)}. IA: {'sí' if ai else 'no'}")
    return 0 if ok > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
