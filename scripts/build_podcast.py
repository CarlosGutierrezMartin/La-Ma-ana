#!/usr/bin/env python3
"""Podcast diario de La Mañana con Gemini (guion + voces), plan gratuito.

Flujo:
  1. Recupera de la web publicada los episodios anteriores (los MP3 no se
     guardan en git, para que el repo no crezca: viven solo en GitHub Pages).
  2. Elige las mejores noticias de los temas de config/podcast.yaml.
  3. Gemini Flash escribe un diálogo entre dos presentadores (~5 min).
  4. Gemini TTS lo locuta con dos voces, en trozos por si hay tope por petición.
  5. ffmpeg lo pasa a MP3 y se actualizan docs/data/podcast.json y docs/podcast.xml.

Modos (--mode):
  auto        genera si hoy aún no hay episodio (el del cron y los push)
  regenerar   genera aunque ya haya episodio hoy (botón "Run workflow")
  solo-guion  escribe el guion y lo muestra en el log, sin gastar voz
  no          solo recupera episodios anteriores y rehace el feed

Si algo falla, el episodio de hoy no sale pero la edición escrita sí: el
workflow ejecuta este paso con continue-on-error.
"""
from __future__ import annotations

import argparse
import base64
import email.utils
import html
import io
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Europe/Madrid")
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
RATE = 24000          # Gemini TTS: 24 kHz, mono, 16 bits
WORDS_PER_MIN = 150   # ritmo de locución de un informativo


def log(msg: str) -> None:
    print(f"[podcast] {msg}", flush=True)


# --------------------------------------------------------------------------- sitio
def site_info() -> tuple[str, str]:
    """(repo "owner/name", URL base de GitHub Pages)."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        try:
            repo = json.loads((ROOT / "docs/data/podcast.json").read_text())["repo"]
        except Exception:  # noqa: BLE001
            repo = "usuario/repo"
    owner, name = repo.split("/", 1)
    return repo, f"https://{owner.lower()}.github.io/{name}/"


def restore_episodes(index: dict, site: str, out_dir: Path, keep_after: str) -> list[dict]:
    """Descarga de la web publicada los MP3 de episodios anteriores."""
    out_dir.mkdir(parents=True, exist_ok=True)
    kept = []
    for ep in index.get("episodes", []):
        if ep["date"] < keep_after:
            continue
        dest = out_dir / ep["file"]
        if not dest.exists():
            try:
                req = urllib.request.Request(site + "podcast/" + ep["file"], headers={"User-Agent": "LaManana/1.0"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    dest.write_bytes(r.read())
            except Exception as ex:  # noqa: BLE001
                log(f"no se pudo recuperar {ep['file']}: {ex}; se quita del feed")
                continue
        kept.append(ep)
    return kept


# --------------------------------------------------------------------------- noticias
def outlet_name(name: str) -> str:
    """'El Dia.com.do' -> 'El Dia': que no se lea "punto com punto do"."""
    return re.sub(r"(\.(com|es|net|org|info|news|co|do|mx|ar|cl|uk|io))+$", "", name.strip(), flags=re.I) or name


TEXT_UA = "Mozilla/5.0 (compatible; LaManana/1.0; lector personal)"


def article_text(url: str) -> str:
    """Texto principal del artículo (para que el guion pueda desarrollar la
    noticia sin inventar). Google News oculta el enlace real: ahí no hay texto."""
    if "news.google.com" in url:
        return ""
    try:
        import trafilatura
        from build_digest import http_get
        txt = trafilatura.extract(http_get(url, TEXT_UA, 15), favor_precision=True, include_comments=False) or ""
        return re.sub(r"\s+", " ", txt).strip()
    except Exception as ex:  # noqa: BLE001
        log(f"sin texto de {url[:70]}: {type(ex).__name__}")
        return ""


# Siglas y palabras demasiado comunes para identificar una historia
GENERIC = {"ai", "ia", "ceo", "cfo", "cmo", "uk", "us", "usa", "eu", "ue", "eeuu", "fda", "ema", "ipo", "tv",
           "the", "for", "and", "los", "las", "del", "con", "por", "una", "new"}


def names(title: str) -> set[str]:
    """Nombres propios del titular (Nvidia, Jensen, Huang...), sin tildes."""
    import unicodedata
    plain = "".join(c for c in unicodedata.normalize("NFKD", title) if not unicodedata.combining(c))
    words = re.findall(r"\b[A-Z][\w&-]{2,}", plain)
    found = {w.lower() for w in words[1:]} | ({words[0].lower()} if words and not plain.startswith(words[0]) else set()) \
        | {w.lower() for w in re.findall(r"\b[A-Z]{2,}\b", plain)}
    return found - GENERIC


def same_story(a: dict, b: dict) -> bool:
    """La misma historia en dos medios (aunque sea en dos idiomas): comparten
    al menos dos nombres propios."""
    return len(names(a["title"]) & names(b["title"])) >= 2


def pick_stories(digest: dict, topic_ids: list[str], n_stories: int = 6, per_topic_pool: int = 3) -> list[dict]:
    """Pocas noticias bien contadas. Garantiza al menos una por tema elegido y
    completa con las de más relevancia; prefiere las que tienen texto completo."""
    import concurrent.futures as cf
    by_id = {s["id"]: s for s in digest["sections"]}
    pool, seen = [], set()
    for t in topic_ids:
        sec = by_id.get(t)
        if not sec:
            continue
        for sid in [x for x in sec["items"] if x not in seen][:per_topic_pool]:
            seen.add(sid)
            it = digest["items"][sid]
            pool.append({"id": sid, "topic_id": t, "topic": sec["name"], "title": it["title"],
                         "source": outlet_name(it["source"]), "url": it["url"], "score": float(it.get("score") or 0),
                         "summary": it.get("ai_summary") or it.get("summary") or "",
                         "also": [outlet_name(a["source"]) for a in it.get("also", [])],
                         "item": {k: it.get(k) for k in ("title", "url", "source", "source_id", "published", "summary", "image", "topics", "score", "also")}})
    with cf.ThreadPoolExecutor(8) as ex:
        for c, txt in zip(pool, ex.map(lambda c: article_text(c["url"]), pool)):
            c["text"] = txt[:2200]
    # prioridad = relevancia; sin texto completo vale menos (se contaría casi solo el titular)
    prio = lambda c: c["score"] * (1.0 if len(c["text"]) >= 600 else 0.6)  # noqa: E731
    chosen = []

    def add(c) -> bool:
        dup = next((x for x in chosen if same_story(x, c)), None)
        if dup:  # misma historia en otro medio: cuenta como cobertura, no como noticia nueva
            if c["source"] != dup["source"] and c["source"] not in dup["also"]:
                dup["also"].append(c["source"])
            if len(c["text"]) > len(dup["text"]) + 400:
                dup["text"] = c["text"]
            return False
        chosen.append(c)
        return True

    for t in topic_ids:                       # 1) la mejor de cada tema
        for c in sorted((c for c in pool if c["topic_id"] == t), key=prio, reverse=True):
            if add(c):
                break
    for c in sorted(pool, key=prio, reverse=True):   # 2) completar por relevancia
        if len(chosen) >= max(n_stories, len(topic_ids)):
            break
        if c not in chosen:
            add(c)
    top = max((c["score"] for c in chosen), default=1) or 1
    for c in chosen:
        c["relevance"] = max(1, round(100 * c["score"] / top))
    order = {t: i for i, t in enumerate(topic_ids)}
    chosen.sort(key=lambda c: (order[c["topic_id"]], -c["score"]))
    return chosen


# --------------------------------------------------------------------------- Gemini
class Busy(RuntimeError):
    """Modelo saturado (503) o sin cupo (429): merece la pena probar otro."""


def gemini(model: str, body: dict, key: str, timeout: int = 300, tries: int = 3) -> dict:
    """POST generateContent con reintentos en 429/5xx."""
    data = json.dumps(body).encode()
    for attempt in range(tries):
        req = urllib.request.Request(API.format(model=model), data=data, method="POST",
                                     headers={"x-goog-api-key": key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as ex:
            detail = ex.read().decode(errors="replace")[:600]
            if ex.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                wait = int(ex.headers.get("Retry-After") or 0) or 20 * (attempt + 1)
                log(f"{model}: HTTP {ex.code}, reintento en {wait}s ({detail[:160]})")
                time.sleep(wait)
                continue
            err = Busy if ex.code in (429, 500, 502, 503, 504) else RuntimeError
            raise err(f"{model}: HTTP {ex.code}: {detail[:300]}") from None
        except (urllib.error.URLError, TimeoutError) as ex:
            if attempt < tries - 1:
                time.sleep(15)
                continue
            raise Busy(f"{model}: sin conexión: {ex}") from None
    raise Busy(f"{model}: sin respuesta tras varios intentos")


def models_of(cfg: dict, kind: str) -> list[str]:
    m = cfg["models"][kind]
    return [m] if isinstance(m, str) else list(m)


BUSY_ROUNDS, BUSY_WAIT = 3, 180   # se ajustan desde config/podcast.yaml


def gemini_any(models: list[str], body: dict, key: str) -> tuple[dict, str]:
    """Prueba los modelos en orden: si uno está saturado o sin cupo, el siguiente.
    Si lo están todos, espera y repite la ronda (el plan gratuito tiene picos)."""
    last = None
    for rnd in range(BUSY_ROUNDS):
        if rnd:
            log(f"todos los modelos saturados; nueva ronda {rnd + 1}/{BUSY_ROUNDS} en {BUSY_WAIT}s")
            time.sleep(BUSY_WAIT)
        for i, model in enumerate(models):
            try:
                return gemini(model, body, key, tries=2), model
            except Busy as ex:
                last = ex
                if i < len(models) - 1:
                    log(f"{model} no disponible ({str(ex)[:100]}); pruebo {models[i + 1]}")
    raise last


def response_text(resp: dict) -> str:
    cands = resp.get("candidates") or []
    if not cands:
        raise RuntimeError(f"respuesta sin contenido: {json.dumps(resp)[:400]}")
    parts = (cands[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def write_script(stories: list[dict], cfg: dict, day_label: str, key: str) -> dict:
    hosts = [h["name"] for h in cfg["hosts"]]
    words = int(cfg.get("minutes", 5) * WORDS_PER_MIN)
    topics_order = list(dict.fromkeys(st["topic"] for st in stories))
    blocks = []
    for i, st in enumerate(stories, 1):
        also = f" (también la publican: {', '.join(st['also'])})" if st["also"] else ""
        body = st["text"] or st["summary"] or "(solo hay titular)"
        blocks.append(f"### Noticia {i} · relevancia {st['relevance']}/100 · tema: {st['topic']}\n"
                      f"Medio: {st['source']}{also}\nTitular: {st['title']}\nTexto: {body}")
    prompt = f"""Escribe el guion del podcast diario "La Mañana" de hoy, {day_label}.

Presentan {hosts[0]} y {hosts[1]}, dos periodistas españoles. Unas {words} palabras en total (unos {cfg.get('minutes', 5)} minutos), en español de España. El oyente quiere INFORMACIÓN: pocas noticias bien explicadas, no un repaso rápido de titulares.

Estructura:
- Apertura: una sola frase con saludo y fecha. Sin presentarse, sin charla ni anticipar los temas.
- Las {len(stories)} noticias de abajo, en ese orden (temas: {', '.join(topics_order)}). Reparte el tiempo según la relevancia: la de 100/100 es la que más se desarrolla; las de relevancia baja, más breves.
- Cierre: una sola frase.

Cómo contar cada noticia, usando SOLO su texto:
- Qué ha pasado, quién, cuándo, las cifras y datos concretos que dé el texto, el contexto que el propio texto explique y por qué importa a alguien de ese sector.
- Si el texto es corto o solo hay titular, cuéntala en dos o tres frases y pasa a la siguiente: mejor breve que rellenar.
- Prohibido inventar o suponer cifras, nombres, fechas, causas, consecuencias o declaraciones que no estén en el texto. No especules ni opines.
- Cita el medio con naturalidad ("según El Global...") y, si otros medios la publican, dilo.
- Traduce al español lo que esté en inglés.

Diálogo:
- Cada intervención aporta un dato o una explicación. Uno presenta la noticia y el otro añade datos del texto o pregunta algo que el texto responde. Nada de reacciones vacías ("¡qué interesante!", "sin duda") ni de bromas.
- {hosts[0]} es {hosts[0]} y {hosts[1]} es {hosts[1]}: cada uno habla como sí mismo.
- Frases cortas, fáciles de escuchar. Sin listas, markdown, URLs ni acotaciones. Medios por su nombre, nunca como dirección web.
- Siglas como se pronuncian si no son evidentes; cifras con palabras cuando ayude a leerlas.
- Turnos de como mucho 90 palabras.

{chr(10).join(blocks)}

Devuelve SOLO un JSON con esta forma:
{{"title": "titular del episodio (máx. 80 caracteres)", "turns": [{{"speaker": "{hosts[0]}", "text": "..."}}, {{"speaker": "{hosts[1]}", "text": "..."}}]}}"""
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"}}
    models = models_of(cfg, "script")
    try:
        resp, model = gemini_any(models, body, key)
    except Busy:
        raise
    except RuntimeError as ex:
        if "HTTP 400" not in str(ex):
            raise
        log("el modelo no acepta responseMimeType; se pide el JSON solo por instrucciones")
        resp, model = gemini_any(models, {"contents": body["contents"]}, key)
    log(f"guion escrito con {model}")
    text = response_text(resp)
    data = json.loads(text[text.index("{"): text.rindex("}") + 1])
    turns = [{"speaker": t["speaker"], "text": re.sub(r"\s+", " ", str(t["text"])).strip()}
             for t in data.get("turns", []) if t.get("speaker") in hosts and str(t.get("text", "")).strip()]
    if len(turns) < 4:
        raise RuntimeError(f"guion demasiado corto ({len(turns)} turnos)")
    return {"title": str(data.get("title") or "La Mañana")[:120], "turns": turns}


def chunk_turns(turns: list[dict], max_chars: int) -> list[list[dict]]:
    chunks, cur, size = [], [], 0
    for t in turns:
        if cur and size + len(t["text"]) > max_chars:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(t)
        size += len(t["text"])
    if cur:
        chunks.append(cur)
    return chunks


def decode_audio(resp: dict) -> bytes:
    """Devuelve PCM 16 bits mono a 24 kHz (acepta WAV con cabecera o L16 crudo)."""
    for part in ((resp.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []:
        inline = part.get("inlineData") or part.get("inline_data")
        if not inline:
            continue
        raw = base64.b64decode(inline["data"])
        if raw[:4] == b"RIFF":
            with wave.open(io.BytesIO(raw)) as w:
                if w.getframerate() != RATE or w.getnchannels() != 1 or w.getsampwidth() != 2:
                    raise RuntimeError(f"formato de audio inesperado: {w.getparams()}")
                return w.readframes(w.getnframes())
        mime = inline.get("mimeType") or inline.get("mime_type") or ""
        m = re.search(r"rate=(\d+)", mime)
        if m and int(m.group(1)) != RATE:
            raise RuntimeError(f"sample rate inesperado: {mime}")
        return raw
    raise RuntimeError(f"la respuesta no trae audio: {json.dumps(resp)[:400]}")


class TTS:
    """Dos formatos de petición multi-voz; recuerda el que funcione."""

    def __init__(self, cfg: dict, key: str):
        self.cfg, self.key, self.fmt, self.model = cfg, key, None, None
        self.voices = {"multiSpeakerVoiceConfig": {"speakerVoiceConfigs": [
            {"speaker": h["name"], "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": h["voice"]}}}
            for h in cfg["hosts"]]}}

    def _body(self, turns: list[dict], fmt: str) -> dict:
        style = self.cfg.get("style", "")
        if fmt == "turnos":     # formato actual de la documentación: un trozo por turno
            parts = [{"text": t["text"], "speechMetadata": {"speaker": t["speaker"], **({"style": style} if style else {})}}
                     for t in turns]
        else:                   # formato clásico: transcripción "Nombre: texto"
            names = " y ".join(h["name"] for h in self.cfg["hosts"])
            head = f"Lee esta conversación entre {names}. {style}".strip()
            parts = [{"text": head + "\n\n" + "\n".join(f"{t['speaker']}: {t['text']}" for t in turns)}]
        return {"contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": self.voices}}

    def speak(self, turns: list[dict]) -> bytes:
        models = models_of(self.cfg, "tts")
        if self.model:  # mismo modelo en todo el episodio para que las voces no cambien
            models = [self.model] + [m for m in models if m != self.model]
        for fmt in ([self.fmt] if self.fmt else ["turnos", "transcripcion"]):
            try:
                resp, model = gemini_any(models, self._body(turns, fmt), self.key)
                pcm = decode_audio(resp)
                self.fmt, self.model = fmt, model
                return pcm
            except Busy:
                raise
            except RuntimeError as ex:
                if self.fmt or "HTTP 400" not in str(ex):
                    raise
                log(f"formato '{fmt}' rechazado ({str(ex)[:200]}); pruebo el otro")
        raise RuntimeError("ningún formato de petición TTS aceptado")


def to_mp3(pcm: bytes, dest: Path, title: str) -> None:
    with tempfile.NamedTemporaryFile(suffix=".pcm") as f:
        f.write(pcm)
        f.flush()
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "s16le", "-ar", str(RATE), "-ac", "1", "-i", f.name,
                        "-codec:a", "libmp3lame", "-b:a", "64k", "-metadata", f"title={title}",
                        "-metadata", "artist=La Mañana", str(dest)], check=True)


# --------------------------------------------------------------------------- feed
def write_feed(index: dict, site: str, dest: Path) -> None:
    def e(s): return html.escape(str(s), quote=True)
    items = []
    for ep in sorted(index["episodes"], key=lambda x: x["date"], reverse=True):
        d = datetime.fromisoformat(ep["published"])
        desc = ep.get("description", "")
        items.append(f"""    <item>
      <title>{e(ep['title'])}</title>
      <description>{e(desc)}</description>
      <pubDate>{email.utils.format_datetime(d)}</pubDate>
      <guid isPermaLink="false">la-manana-{e(ep['date'])}</guid>
      <enclosure url="{e(site + 'podcast/' + ep['file'])}" length="{ep['bytes']}" type="audio/mpeg"/>
      <itunes:duration>{int(ep['duration'])}</itunes:duration>
      <itunes:explicit>false</itunes:explicit>
    </item>""")
    dest.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>La Mañana</title>
    <link>{e(site)}</link>
    <atom:link href="{e(site + 'podcast.xml')}" rel="self" type="application/rss+xml"/>
    <language>es-es</language>
    <description>Las noticias que te importan, en unos minutos. Guion y voces generados con IA (Google Gemini) a partir de titulares de prensa; cada episodio cita sus medios.</description>
    <itunes:author>La Mañana</itunes:author>
    <itunes:image href="{e(site + 'icons/icon-512.png')}"/>
    <itunes:category text="News"/>
    <itunes:explicit>false</itunes:explicit>
    <itunes:block>Yes</itunes:block>
{chr(10).join(items)}
  </channel>
</rss>
""", encoding="utf-8")


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="auto", choices=["auto", "regenerar", "solo-guion", "no"])
    ap.add_argument("--config", default=ROOT / "config/podcast.yaml", type=Path)
    ap.add_argument("--digest", default=ROOT / "docs/data/latest.json", type=Path)
    ap.add_argument("--docs", default=ROOT / "docs", type=Path)
    args = ap.parse_args()

    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    global BUSY_ROUNDS, BUSY_WAIT
    BUSY_ROUNDS = int(cfg.get("busy_rounds", BUSY_ROUNDS))
    BUSY_WAIT = int(cfg.get("busy_wait_seconds", BUSY_WAIT))
    repo, site = site_info()
    audio_dir, index_path = args.docs / "podcast", args.docs / "data/podcast.json"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        index = {"episodes": []}
    digest = json.loads(args.digest.read_text(encoding="utf-8"))
    day = digest["date"]
    keep_after = (datetime.fromisoformat(day).date() - timedelta(days=int(cfg.get("retention_days", 14)))).isoformat()

    index["episodes"] = restore_episodes(index, site, audio_dir, keep_after)
    index.update(repo=repo, site=site, feed=site + "podcast.xml", topics_next=cfg.get("topics", []))

    def save():
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
        write_feed(index, site, args.docs / "podcast.xml")

    try:
        return generate(args, cfg, index, digest, day, audio_dir, save)
    except Exception as ex:  # noqa: BLE001
        # Sin episodio hoy, pero el feed y los episodios anteriores se publican igual
        log(f"ERROR: {type(ex).__name__}: {ex}")
        save()
        return 1


def generate(args, cfg, index, digest, day, audio_dir, save) -> int:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    has_today = any(ep["date"] == day for ep in index["episodes"])
    if args.mode == "no" or (args.mode == "auto" and has_today):
        log("hoy ya hay episodio" if has_today else "modo 'no': solo se rehace el feed")
        save()
        return 0
    if not key:
        log("sin GEMINI_API_KEY: no hay podcast (la edición escrita sale igual)")
        save()
        return 0

    topic_ids = [t for t in cfg.get("topics", []) if any(s["id"] == t for s in digest["sections"])]
    if not topic_ids:
        raise SystemExit("[podcast] ninguno de los temas de config/podcast.yaml existe en config/topics.yaml")
    stories = pick_stories(digest, topic_ids, int(cfg.get("stories", 6)))
    log(f"temas: {', '.join(topic_ids)} · {len(stories)} noticias")
    for st in stories:
        log(f"  {st['relevance']:>3}/100 · {len(st['text']):>5} car. · {st['source']}: {st['title'][:70]}")
    day_label = datetime.fromisoformat(day).strftime("%d/%m/%Y")
    script = write_script(stories, cfg, day_label, key)
    n_words = sum(len(t["text"].split()) for t in script["turns"])
    log(f"guion: «{script['title']}», {len(script['turns'])} turnos, {n_words} palabras")
    if args.mode == "solo-guion":
        for t in script["turns"]:
            print(f"  {t['speaker']}: {t['text']}")
        save()
        return 0

    tts = TTS(cfg, key)
    chunks = chunk_turns(script["turns"], int(cfg.get("max_chars_per_request", 2500)))
    silence = b"\x00\x00" * int(RATE * 0.35)
    pcm = b""
    for i, ch in enumerate(chunks):
        if i:
            time.sleep(int(cfg.get("seconds_between_requests", 25)))  # cupo por minuto del plan gratuito
        log(f"voz: trozo {i + 1}/{len(chunks)} ({sum(len(t['text']) for t in ch)} caracteres)")
        pcm += (silence if pcm else b"") + tts.speak(ch)
    duration = len(pcm) / 2 / RATE

    audio_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{day}.mp3"
    to_mp3(pcm, audio_dir / fname, script["title"])
    sources = sorted({s["source"] for s in stories})
    names = {s["id"]: s["name"] for s in digest["sections"]}
    episode = {
        "date": day, "file": fname, "title": script["title"], "duration": round(duration),
        "bytes": (audio_dir / fname).stat().st_size, "published": datetime.now(timezone.utc).isoformat(),
        "topics": topic_ids, "topic_names": [names[t] for t in topic_ids], "stories": [s["id"] for s in stories],
        # ficha de cada noticia: la pestaña Podcast funciona aunque la edición se regenere después
        "story_items": [{"id": s["id"], "relevance": s["relevance"], **s["item"]} for s in stories],
        "description": f"Temas: {', '.join(names[t] for t in topic_ids)}. Medios: {', '.join(sources)}. "
                       f"Guion y voces generados con IA (Google Gemini) a partir de titulares.",
        "transcript": script["turns"],
    }
    index["episodes"] = [ep for ep in index["episodes"] if ep["date"] != day] + [episode]
    index["episodes"].sort(key=lambda x: x["date"], reverse=True)
    save()
    log(f"listo: {fname} · {duration / 60:.1f} min · {episode['bytes'] / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
