# La Mañana

Periódico personal en forma de PWA. Cada mañana un job de GitHub Actions lee los RSS de tus medios, agrupa la misma noticia cuando la publican varios, la puntúa según tus temas y publica la edición en GitHub Pages. Tú solo abres la app en el móvil.

```
config/sources.yaml   medios y feeds RSS (con respaldo de Google News)
config/topics.yaml    tus temas, palabras clave, exclusiones y resumen IA
scripts/build_digest.py  descarga, agrupa, puntúa y escribe docs/data/*.json
docs/                 la PWA (HTML + CSS + JS sin dependencias) y los datos
.github/workflows/daily.yml  cron diario + despliegue en Pages
```

## Puesta en marcha (10 minutos)

1. Crea un repositorio en GitHub y sube esta carpeta a la rama `main`.
2. **Settings → Pages → Source: GitHub Actions.**
3. **Settings → Actions → General → Workflow permissions: Read and write.**
4. (Opcional) **Settings → Secrets and variables → Actions → New secret** `ANTHROPIC_API_KEY`. Con ella aparecen "Las claves del día" y un resumen de 1-2 frases por noticia. Sin ella la app funciona igual con las entradillas del RSS.
5. **Actions → Edición diaria → Run workflow** para la primera edición. Revisa el log: verás qué feeds han respondido y cuáles han ido por Google News.
6. Abre `https://<usuario>.github.io/<repo>/` en el móvil y "Añadir a pantalla de inicio" (Safari: compartir; Chrome: menú ⋮ → Instalar app).

A partir de ahí se regenera sola todos los días a las 06:20 (07:20 en horario de verano). Cambia la hora en el `cron` del workflow.

## Personalizar

- **Temas**: edita `config/topics.yaml` (se puede hacer desde la web de GitHub en el móvil). Cada cambio relanza el job y ves el efecto en un minuto.
- **Medios**: añade bloques en `config/sources.yaml`. Si un medio no tiene RSS, deja `feeds: []` y pon `fallback_gnews: "site:dominio.com"`.
- **En el móvil** (Ajustes ⚙︎): tema papel/noche, tamaño de letra, ocultar leídas, ocultar temas y ocultar medios (se guarda en cada dispositivo).
- **Medios especializados**: con `topics: [id]` en `sources.yaml` todas sus noticias cuentan para ese tema; `window_hours` les da una ventana más larga si publican poco; `search:` crea una fuente temática con una búsqueda de Google News.

## Podcast diario (opcional, Gemini gratis)

Con el secreto `GEMINI_API_KEY` (Google AI Studio, plan gratuito), cada edición genera además un episodio de ~10 minutos a dos voces:

1. Para cada tema elegido en `config/podcast.yaml` toma sus 3 mejores noticias y lee el texto del artículo (`trafilatura`). Elige 9: la mejor de cada tema y el resto por relevancia, prefiriendo las que tienen texto completo y quitando la misma historia contada por dos medios.
2. Gemini Flash escribe el guion solo con esos textos, con la relevancia de cada noticia (0-100) para repartir el tiempo; Gemini TTS lo locuta. Si un modelo está saturado, prueba el siguiente de la lista.
3. El MP3 se publica en Pages (no se guarda en git) con su feed en `podcast.xml`.

Los temas se cambian desde la app (Ajustes → Podcast, con un token de GitHub de este repo con permiso Contents: Read and write) o editando `config/podcast.yaml`; se aplican al día siguiente. "Run workflow" permite regenerar el episodio, ver solo el guion en el log o no gastar cupo.

## Cómo decide qué es relevante

`puntuación = (coincidencias con tus temas × peso del tema + 4 por cada otro medio que publica la misma historia + 2 si está en portada) × peso del medio × recencia`

Las coincidencias en el titular valen el triple que en la entradilla. La historia que dan cuatro periódicos sube sola, aunque no toque tus temas.

## Privacidad

En la cuenta gratuita de GitHub, Pages exige repositorio público: cualquiera con la URL ve tus temas y titulares. Si prefieres que sea privado, conecta el repo a Cloudflare Pages (gratis con repos privados, directorio de salida `docs`) y protégelo con Cloudflare Access.

## Probar en local

```bash
pip install -r requirements.txt
python scripts/build_digest.py            # escribe docs/data/
python -m http.server -d docs 8000        # http://localhost:8000
```

Tests con feeds falsos (sin red): `python tests/make_fixtures.py && python scripts/build_digest.py --sources tests/fixtures/sources.test.yaml --out /tmp/digest --now 2026-11-23T05:15:00+00:00`
