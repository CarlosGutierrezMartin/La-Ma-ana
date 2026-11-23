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
def pick_stories(digest: dict, topic_ids: list[str], total: int = 12) -> list[dict]:
    """Las mejores noticias de cada tema elegido, sin repetir, repartidas."""
    by_id = {s["id"]: s for s in digest["sections"]}
    per_topic = max(2, min(4, math.ceil(total / max(1, len(topic_ids)))))
    used, out = set(), []
    for t in topic_ids:
        sec = by_id.get(t)
        if not sec:
            continue
        n = 0
        for sid in sec["items"]:
            if sid in used:
                continue
            it = digest["items"][sid]
            used.add(sid)
            out.append({"id": sid, "topic": sec["name"], "title": it["title"], "source": it["source"],
                        "summary": it.get("ai_summary") or it.get("summary") or "",
                        "also": [a["source"] for a in it.get("also", [])]})
            n += 1
            if n >= per_topic:
                break
    return out


# --------------------------------------------------------------------------- Gemini
def gemini(model: str, body: dict, key: str, timeout: int = 300) -> dict:
    """POST generateContent con reintentos en 429/5xx."""
    data = json.dumps(body).encode()
    for attempt in range(4):
        req = urllib.request.Request(API.format(model=model), data=data, method="POST",
                                     headers={"x-goog-api-key": key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as ex:
            detail = ex.read().decode(errors="replace")[:600]
            if ex.code in (429, 500, 502, 503, 504) and attempt < 3:
                wait = int(ex.headers.get("Retry-After") or 0) or 20 * (attempt + 1)
                log(f"{model}: HTTP {ex.code}, reintento en {wait}s ({detail[:160]})")
                time.sleep(wait)
                continue
            raise RuntimeError(f"{model}: HTTP {ex.code}: {detail}") from None
    raise RuntimeError(f"{model}: sin respuesta tras varios intentos")


def response_text(resp: dict) -> str:
    cands = resp.get("candidates") or []
    if not cands:
        raise RuntimeError(f"respuesta sin contenido: {json.dumps(resp)[:400]}")
    parts = (cands[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def write_script(stories: list[dict], cfg: dict, day_label: str, key: str) -> dict:
    hosts = [h["name"] for h in cfg["hosts"]]
    words = int(cfg.get("minutes", 5) * WORDS_PER_MIN)
    topics_order = []
    for s in stories:
        if s["topic"] not in topics_order:
            topics_order.append(s["topic"])
    lines = []
    for s in stories:
        extra = f" (también en {', '.join(s['also'])})" if s["also"] else ""
        lines.append(f"- [{s['topic']}] {s['source']}{extra}: {s['title']}" + (f". {s['summary'][:350]}" if s["summary"] else ""))
    prompt = f"""Escribe el guion del podcast diario "La Mañana" de hoy, {day_label}.

Presentan {hosts[0]} y {hosts[1]}, dos periodistas españoles, en una conversación natural de unos {words} palabras en total (unos {cfg.get('minutes', 5)} minutos). Español de España.

Temas, en este orden: {', '.join(topics_order)}. Trata TODOS los temas, cada uno con al menos una noticia, dedicando más tiempo a las noticias de mayor peso.

Noticias disponibles (tema, medio, titular y entradilla):
{chr(10).join(lines)}

Reglas:
- Usa SOLO la información de estas noticias. No inventes cifras, nombres, fechas ni declaraciones; si un dato no está, no lo des.
- Cita el medio de cada noticia de forma natural ("según Diario Médico...").
- Traduce al español las noticias en inglés.
- Empieza saludando y diciendo la fecha; termina con una despedida breve.
- Frases cortas, fáciles de escuchar. Sin listas, sin markdown, sin URLs, sin acotaciones entre paréntesis.
- Escribe las siglas como se pronuncian si no son evidentes, y las cifras con palabras cuando ayude a leerlas.
- Alterna los turnos; ninguno de más de 80 palabras.

Devuelve SOLO un JSON con esta forma:
{{"title": "titular del episodio (máx. 80 caracteres)", "turns": [{{"speaker": "{hosts[0]}", "text": "..."}}, {{"speaker": "{hosts[1]}", "text": "..."}}]}}"""
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"}}
    try:
        resp = gemini(cfg["models"]["script"], body, key)
    except RuntimeError as ex:
        if "HTTP 400" not in str(ex):
            raise
        log("el modelo no acepta responseMimeType; se pide el JSON solo por instrucciones")
        resp = gemini(cfg["models"]["script"], {"contents": body["contents"]}, key)
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
        self.cfg, self.key, self.fmt = cfg, key, None
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
        model = self.cfg["models"]["tts"]
        for fmt in ([self.fmt] if self.fmt else ["turnos", "transcripcion"]):
            try:
                pcm = decode_audio(gemini(model, self._body(turns, fmt), self.key))
                self.fmt = fmt
                return pcm
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
    stories = pick_stories(digest, topic_ids)
    log(f"temas: {', '.join(topic_ids)} · {len(stories)} noticias")
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
