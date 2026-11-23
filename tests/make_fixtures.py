"""Genera feeds RSS de prueba (file://) y una config que apunta a ellos."""
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import yaml

HERE = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 23, 5, 15, tzinfo=timezone.utc)

FEEDS = {
    "elpais": [
        ("La UE aplaza la aplicación del reglamento de IA para los modelos de propósito general", "Bruselas da un año más a OpenAI, Google y Anthropic para cumplir las obligaciones de transparencia de la ley europea de inteligencia artificial.", 3),
        ("El BCE mantiene los tipos de interés y avisa del riesgo de los aranceles", "Lagarde señala que la inflación se mantiene cerca del objetivo del 2%.", 6),
        ("Telefónica cierra la compra de su filial alemana tras meses de negociación", "La operadora paga 2.100 millones y refuerza su apuesta por Europa.", 9),
        ("El Real Madrid golea en la Champions", "Crónica del partido.", 2),
    ],
    "elmundo": [
        ("Bruselas aplaza el reglamento de IA para los grandes modelos de propósito general", "La Comisión Europea concede un año extra a las tecnológicas.", 4),
        ("Sánchez presentará los presupuestos en el Congreso antes de fin de año", "El Gobierno negocia con sus socios.", 5),
        ("Inditex dispara su beneficio un 12% en el tercer trimestre", "La empresa supera las previsiones de los analistas.", 7),
    ],
    "elconfidencial": [
        ("Nvidia vende más chips que nunca: los centros de datos disparan su facturación", "Los resultados de la compañía superan otra vez las expectativas por la demanda de GPU para IA generativa.", 8),
        ("Telefónica completa la compra de su filial en Alemania por 2.100 millones", "Operación clave del plan estratégico.", 10),
        ("La vivienda sube un 9% y el paro baja en Madrid", "Datos del INE.", 12),
    ],
    "eleconomista": [
        ("El Ibex cae arrastrado por la banca tras la decisión del BCE sobre los tipos de interés", "Santander y BBVA lideran las pérdidas.", 5),
        ("Startup española de agentes de IA cierra una ronda de financiación de 40 millones", "La compañía madrileña desarrolla agentes de IA para empresas.", 11),
    ],
    "techcrunch": [
        ("Anthropic launches new Claude model with stronger AI agent capabilities", "The release targets enterprise customers building AI agents.", 2),
        ("AI startup raises $200M funding round at $2B valuation", "The company builds data center software for LLM inference.", 14),
    ],
    "mittr": [
        ("Why the EU AI Act delay matters for general-purpose models", "The European Commission gave AI companies another year.", 6),
        ("Old story outside the window", "Should be filtered.", 60),
    ],
}


def rss(items):
    out = ['<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel><title>t</title>']
    for i, (title, desc, hours) in enumerate(items):
        pub = format_datetime(NOW - timedelta(hours=hours))
        slug = abs(hash(title)) % 10**8
        img = f'<media:content url="https://picsum.photos/seed/{slug}/400/240" medium="image"/>' if i % 2 == 0 else ""
        out.append(f"<item><title><![CDATA[{title}]]></title><link>https://example.com/{slug}</link><description><![CDATA[<p>{desc}</p>]]></description><pubDate>{pub}</pubDate>{img}</item>")
    out.append("</channel></rss>")
    return "".join(out)


def main():
    HERE.mkdir(exist_ok=True)
    cfg = yaml.safe_load((Path(__file__).parent.parent / "config/sources.yaml").read_text())
    for s in cfg["sources"]:
        if s["id"] in FEEDS:
            p = HERE / f"{s['id']}.xml"
            p.write_text(rss(FEEDS[s["id"]]), encoding="utf-8")
            s["feeds"] = [{"url": p.resolve().as_uri(), "frontpage": s["id"] in ("elpais", "elmundo")}]
        else:
            s["feeds"] = [{"url": (HERE / "missing.xml").resolve().as_uri()}]
            s.pop("fallback_gnews", None)
    (HERE / "sources.test.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    print("now:", NOW.isoformat())


if __name__ == "__main__":
    main()
