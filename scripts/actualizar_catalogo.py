#!/usr/bin/env python3
"""Actualiza el catálogo de piezas educativas de index.html leyendo hic.fcv.org.

Qué hace
  1. Lee la página principal de educación al paciente y descubre sus categorías.
  2. Lee cada categoría y compara las piezas con las que ya están en index.html.
  3. Agrega las piezas nuevas y quita las que ya no existen en el sitio.
     Las piezas que ya estaban NO se reescriben (conservan título y etiqueta).

Seguridad
  - Respeta el robots.txt de hic.fcv.org; si no permite leer, termina sin cambiar nada.
  - Solo acepta enlaces de https://hic.fcv.org.
  - Si algo se ve raro (el sitio cambió de estructura, una categoría quedó casi vacía,
    el total cae más de 20 %), conserva lo que había y avisa.

Uso
  python scripts/actualizar_catalogo.py              # actualiza index.html
  python scripts/actualizar_catalogo.py --dry-run    # solo muestra qué cambiaría

Códigos de salida: 0 = ok (con o sin cambios), 2 = no se actualizó por una
salvaguarda, 3 = robots.txt no permite la lectura o el sitio no respondió.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

BASE = "https://hic.fcv.org"
HOST = "hic.fcv.org"
MAIN_URL = BASE + "/co/educacion-al-paciente-y-su-familia"
USER_AGENT = "materiales-educativos-catalogo/1.0 (+https://github.com/jcristancho2/materiales-educativos)"

INDEX_PATH = Path(__file__).resolve().parent.parent / "index.html"
INICIO = "/* CATALOGO:INICIO"
FIN = "/* CATALOGO:FIN */"

DEFAULT_ICON = "ic-clipboard"
DEFAULT_COLOR = "#64748B"
DELAY_SEG = 1.5
MAX_PAGINAS = 15
MAX_TITULO = 200
GENERICOS = {"ver", "leer", "mas", "más", "abrir", "descargar", "ver más", "leer más",
             "ver mas", "leer mas", "más información", "mas informacion", "ver pieza"}


class RobotsNoPermite(Exception):
    pass


class SitioNoResponde(Exception):
    pass


# --------------------------------------------------------------------------- red

class Fetcher:
    """Lee páginas del HIC respetando robots.txt y con pausa entre peticiones."""

    def __init__(self, delay=DELAY_SEG):
        self.delay = delay
        self.rp = urllib.robotparser.RobotFileParser()
        self._ultimo = 0.0

    def _pedir(self, url):
        espera = self.delay - (time.time() - self._ultimo)
        if espera > 0:
            time.sleep(espera)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "es"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                charset = r.headers.get_content_charset() or "utf-8"
                return r.read().decode(charset, "replace")
        finally:
            self._ultimo = time.time()

    def cargar_robots(self):
        try:
            texto = self._pedir(BASE + "/robots.txt")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403) or e.code >= 500:
                raise RobotsNoPermite(f"robots.txt respondió {e.code}; por precaución no se lee el sitio")
            self.rp.parse([])  # 404 y similares: sin restricciones declaradas
            return
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SitioNoResponde(f"no se pudo leer robots.txt: {e}")
        self.rp.parse(texto.splitlines())

    def get(self, url):
        if not self.rp.can_fetch(USER_AGENT, url):
            raise RobotsNoPermite(f"robots.txt no permite leer {url}")
        ultimo = None
        for intento in range(3):
            try:
                return self._pedir(url)
            except urllib.error.HTTPError as e:
                ultimo = e
                if e.code in (404, 410):
                    break
                time.sleep(2 * (intento + 1))
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                ultimo = e
                time.sleep(2 * (intento + 1))
        raise SitioNoResponde(f"{url}: {ultimo}")


# ------------------------------------------------------------------------ parseo

def limpiar(texto):
    texto = re.sub(r"\s+", " ", texto or "").strip()
    return texto.replace("<", "").replace(">", "")


class Enlaces(HTMLParser):
    """Recolecta los <a> de una página: href, texto visible, title/aria-label y alt de imagen."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self._cur = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self._cur = {"href": a["href"], "texto": [], "title": a.get("title") or a.get("aria-label") or "",
                         "alt": "", "rel": a.get("rel") or ""}
        elif tag == "img" and self._cur is not None and a.get("alt"):
            self._cur["alt"] = a["alt"]
        elif tag == "link" and (a.get("rel") or "").lower() == "next" and a.get("href"):
            self.links.append({"href": a["href"], "texto": "", "title": "", "alt": "", "rel": "next"})

    def handle_data(self, data):
        if self._cur is not None:
            self._cur["texto"].append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._cur is not None:
            c = self._cur
            c["texto"] = limpiar(" ".join(c["texto"]))
            c["title"] = limpiar(c["title"])
            c["alt"] = limpiar(c["alt"])
            self.links.append(c)
            self._cur = None


def parsear(html_texto):
    p = Enlaces()
    p.feed(html_texto)
    p.close()
    return p.links


def normalizar(href, base):
    """URL absoluta https del HIC sin query ni fragmento; None si es de otro sitio."""
    u = urlparse(urljoin(base, href.strip()))
    if u.scheme not in ("http", "https") or u.netloc.lower() != HOST:
        return None
    return f"https://{HOST}{u.path.rstrip('/')}"


def descubrir_categorias(links, slugs_conocidos):
    """{slug: {"url":..., "nombre":...}} a partir de los enlaces de la página principal."""
    principal = urlparse(MAIN_URL).path.rstrip("/")
    cats = {}
    for l in links:
        url = normalizar(l["href"], MAIN_URL)
        if not url:
            continue
        ruta = urlparse(url).path
        if "/material-educativo/" in ruta:
            continue
        slug = ruta.rsplit("/", 1)[-1]
        es_hija = ruta.startswith(principal + "/") and ruta.count("/") == principal.count("/") + 1
        if es_hija or slug in slugs_conocidos:
            nombre = l["texto"] or l["title"] or slug.replace("-", " ").capitalize()
            cats.setdefault(slug, {"url": url, "nombre": nombre})
    return cats


def extraer_piezas(links, pagina_url):
    """Lista [(titulo, url)] de enlaces a /material-educativo/ en una página de categoría."""
    candidatos = {}
    for l in links:
        url = normalizar(l["href"], pagina_url)
        if not url or "/material-educativo/" not in urlparse(url).path:
            continue
        lista = candidatos.setdefault(url, {"title": [], "texto": [], "alt": []})
        for k in ("title", "texto", "alt"):
            if l[k]:
                lista[k].append(l[k])

    def util(t):
        return t.lower().strip(" →>»") not in GENERICOS and 4 <= len(t) <= MAX_TITULO

    piezas = []
    for url, c in candidatos.items():
        titulo = None
        for fuente in ("title", "texto", "alt"):
            buenos = [t for t in c[fuente] if util(t)]
            if buenos:
                titulo = buenos[0] if fuente == "title" else min(buenos, key=len)
                break
        piezas.append((titulo, url))
    return piezas


def paginas_siguientes(links, pagina_url):
    """Enlaces de paginación de la misma categoría (rel=next o ?page=N)."""
    base = urlparse(pagina_url)
    siguientes = []
    for l in links:
        u = urlparse(urljoin(pagina_url, l["href"]))
        if u.netloc.lower() != HOST or u.path.rstrip("/") != base.path.rstrip("/"):
            continue
        q = parse_qs(u.query)
        if "next" in l["rel"].split() or "page" in q or "pagina" in q or "p" in q:
            siguientes.append(f"https://{HOST}{u.path}" + (f"?{u.query}" if u.query else ""))
    return siguientes


def leer_categoria(fetcher, url):
    """Piezas de una categoría, siguiendo la paginación si existe."""
    vistas, pendientes, piezas = set(), [url], {}
    while pendientes and len(vistas) < MAX_PAGINAS:
        actual = pendientes.pop(0)
        if actual in vistas:
            continue
        vistas.add(actual)
        links = parsear(fetcher.get(actual))
        for titulo, u in extraer_piezas(links, actual):
            if u not in piezas or (piezas[u] is None and titulo):
                piezas[u] = titulo
        pendientes.extend(s for s in paginas_siguientes(links, actual) if s not in vistas)
    return list(piezas.items())  # [(url, titulo|None)]


# ---------------------------------------------------------------- bloque de datos

def js(x):
    return json.dumps(x, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def volcar_bloque(cats):
    lineas = ["  var specialties = ["]
    for i, c in enumerate(cats):
        cabecera = '    {"slug":%s,"nombre":%s,"icon":%s,"color":%s,"items":[' % (
            js(c["slug"]), js(c["nombre"]), js(c["icon"]), js(c["color"]))
        lineas.append(cabecera)
        if c["items"]:
            lineas.append(",\n".join("      " + js(it) for it in c["items"]))
        lineas.append("    ]}" + ("," if i < len(cats) - 1 else ""))
    lineas.append("  ];")
    return "\n".join(lineas)


PATRON_BLOQUE = re.compile(
    r"(?P<ini>[ \t]*" + re.escape(INICIO) + r"[^\n]*\*/\n)(?P<cuerpo>.*?)(?P<fin>\n[ \t]*" + re.escape(FIN) + ")",
    re.S)


def leer_catalogo(html_index):
    m = PATRON_BLOQUE.search(html_index)
    if not m:
        raise ValueError("index.html no tiene los marcadores CATALOGO:INICIO / CATALOGO:FIN")
    j = re.search(r"var specialties = (\[.*\]);", m.group("cuerpo"), re.S)
    if not j:
        raise ValueError("no se encontró 'var specialties' dentro del bloque del catálogo")
    return json.loads(j.group(1))


def escribir_catalogo(html_index, cats):
    m = PATRON_BLOQUE.search(html_index)
    nuevo = m.group("ini") + volcar_bloque(cats) + m.group("fin")
    return html_index[:m.start()] + nuevo + html_index[m.end():]


# ------------------------------------------------------------------- actualización

def total(cats):
    return sum(len(c["items"]) for c in cats)


def actualizar(cats, fetcher, log=print):
    """Devuelve (catalogo_nuevo, resumen). Lanza ValueError si una salvaguarda lo impide."""
    log(f"Leyendo {MAIN_URL}")
    links = parsear(fetcher.get(MAIN_URL))
    conocidas = {c["slug"] for c in cats}
    sitio = descubrir_categorias(links, conocidas)
    if not sitio:
        raise ValueError("no se descubrió ninguna categoría en la página principal; "
                         "es probable que el sitio haya cambiado de estructura")
    log(f"Categorías en el sitio: {len(sitio)} (en el catálogo: {len(cats)})")

    nuevo, resumen, avisos = [], [], []
    antes = total(cats)

    for c in cats:
        c2 = {**c, "items": [list(i) for i in c["items"]]}
        info = sitio.get(c["slug"])
        if not info:
            avisos.append(f"{c['slug']}: no aparece en el sitio; se conserva sin cambios")
            nuevo.append(c2)
            continue
        try:
            leidas = leer_categoria(fetcher, info["url"])
        except SitioNoResponde as e:
            avisos.append(f"{c['slug']}: no se pudo leer ({e}); se conserva sin cambios")
            nuevo.append(c2)
            continue
        urls_sitio = {u for u, _ in leidas}
        previas = len(c["items"])
        if previas and not urls_sitio:
            avisos.append(f"{c['slug']}: la lectura devolvió 0 piezas; se conserva sin cambios")
            nuevo.append(c2)
            continue
        if previas >= 4 and len(urls_sitio) < previas * 0.5:
            avisos.append(f"{c['slug']}: la lectura devolvió {len(urls_sitio)} de {previas} piezas; "
                          "parece un error de lectura, se conserva sin cambios")
            nuevo.append(c2)
            continue
        existentes = {i[1] for i in c["items"]}
        quitadas = [i for i in c["items"] if i[1] not in urls_sitio]
        c2["items"] = [list(i) for i in c["items"] if i[1] in urls_sitio]
        agregadas = []
        for url, titulo in leidas:
            if url in existentes:
                continue
            if not titulo:
                avisos.append(f"{c['slug']}: pieza nueva sin título legible, se omite: {url}")
                continue
            c2["items"].append([titulo, url, c["nombre"]])
            agregadas.append(titulo)
        if agregadas or quitadas:
            resumen.append((c["nombre"], agregadas, [q[0] for q in quitadas]))
        nuevo.append(c2)

    for slug, info in sitio.items():
        if slug in conocidas:
            continue
        try:
            leidas = leer_categoria(fetcher, info["url"])
        except SitioNoResponde as e:
            avisos.append(f"{slug}: categoría nueva, no se pudo leer ({e})")
            continue
        items = [[t, u, info["nombre"]] for u, t in leidas if t]
        if items:
            nuevo.append({"slug": slug, "nombre": info["nombre"], "icon": DEFAULT_ICON,
                          "color": DEFAULT_COLOR, "items": items})
            resumen.append((info["nombre"] + " (categoría nueva)", [i[0] for i in items], []))

    despues = total(nuevo)
    if antes and despues < antes * 0.8:
        raise ValueError(f"el total caería de {antes} a {despues} piezas (más de 20 %); no se actualiza")
    for a in avisos:
        log("AVISO: " + a)
    return nuevo, resumen


def texto_resumen(resumen):
    sumadas = sum(len(a) for _, a, _ in resumen)
    quitadas = sum(len(q) for _, _, q in resumen)
    return f"+{sumadas} / -{quitadas} piezas"


def ejecutar(index_path, fetcher, dry_run=False, log=print):
    html_index = Path(index_path).read_text(encoding="utf-8")
    cats = leer_catalogo(html_index)
    fetcher.cargar_robots()
    nuevo, resumen = actualizar(cats, fetcher, log)
    for nombre, agregadas, quitadas in resumen:
        log(f"  {nombre}")
        for t in agregadas:
            log(f"    + {t}")
        for t in quitadas:
            log(f"    - {t}")
    if not resumen:
        log("Sin cambios: el catálogo ya está al día.")
        return 0, ""
    salida = texto_resumen(resumen)
    log(f"Resultado: {salida}")
    if dry_run:
        log("(--dry-run: no se escribió index.html)")
    else:
        Path(index_path).write_text(escribir_catalogo(html_index, nuevo), encoding="utf-8")
    return 0, salida


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="muestra los cambios sin escribir index.html")
    ap.add_argument("--index", default=str(INDEX_PATH), help="ruta de index.html")
    args = ap.parse_args(argv)
    try:
        codigo, resumen = ejecutar(args.index, Fetcher(), args.dry_run)
    except (RobotsNoPermite, SitioNoResponde) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 3
    except ValueError as e:
        print(f"ERROR (no se modificó nada): {e}", file=sys.stderr)
        return 2
    salida = os.environ.get("GITHUB_OUTPUT")
    if salida and resumen:
        with open(salida, "a", encoding="utf-8") as f:
            f.write(f"resumen={resumen}\n")
    return codigo


if __name__ == "__main__":
    sys.exit(main())
