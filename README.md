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
- **En el móvil** (Ajustes ⚙︎): tema papel/noche, tamaño de letra, ocultar leídas, ocultar medios.

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
