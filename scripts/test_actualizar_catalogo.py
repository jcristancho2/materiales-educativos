"""Pruebas del actualizador de catálogo (sin red: usan un Fetcher falso).

Ejecutar:  python -m unittest discover -s scripts -v
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import actualizar_catalogo as m  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
P = "https://hic.fcv.org/co/material-educativo/"
CAT = m.MAIN_URL + "/"


class FalsoFetcher:
    def __init__(self, paginas, caidas=()):
        self.paginas, self.caidas = paginas, set(caidas)

    def cargar_robots(self):
        pass

    def get(self, url):
        if url in self.caidas or url not in self.paginas:
            raise m.SitioNoResponde(url)
        return self.paginas[url]


def pagina_cat(*piezas, extra=""):
    filas = "".join(f'<div class="card"><img src="x.png" alt="img"><a href="{u}">{t}</a>'
                    f'<a href="{u}">Ver más</a></div>' for t, u in piezas)
    return f"<html><body>{filas}{extra}</body></html>"


def principal(*slugs):
    return "<html><body>" + "".join(f'<a href="/co/educacion-al-paciente-y-su-familia/{s}">{s.title()}</a>'
                                    for s in slugs) + "</body></html>"


def catalogo():
    return [
        {"slug": "cardio", "nombre": "Cardio", "icon": "ic-heart", "color": "#97113A",
         "items": [["Pieza A", P + "a", "Tag A"], ["Pieza B", P + "b", "Tag B"],
                   ["Pieza C", P + "c", "Tag C"], ["Pieza D", P + "d", "Tag D"]]},
        {"slug": "mente", "nombre": "Mente", "icon": "ic-mind", "color": "#721739",
         "items": [["Pieza M", P + "m", "Tag M"]]},
    ]


class Extraccion(unittest.TestCase):
    def test_titulo_ignora_enlaces_genericos_y_otros_sitios(self):
        html = pagina_cat(("Guía nueva", P + "g1")) + '<a href="https://otro.com/material-educativo/x">Mala</a>'
        piezas = m.extraer_piezas(m.parsear(html), CAT + "cardio")
        self.assertEqual(piezas, [("Guía nueva", P + "g1")])

    def test_titulo_sin_etiquetas_html(self):
        html = f'<a href="{P}z">Pieza <b>con</b> &lt;script&gt; marca</a>'
        (titulo, _), = m.extraer_piezas(m.parsear(html), CAT + "cardio")
        self.assertNotIn("<", titulo)
        self.assertNotIn(">", titulo)

    def test_descubre_categorias(self):
        cats = m.descubrir_categorias(m.parsear(principal("cardio", "mente")), {"cardio"})
        self.assertEqual(set(cats), {"cardio", "mente"})

    def test_paginacion(self):
        html = f'<a href="{CAT}cardio?page=2">2</a><a href="{CAT}mente?page=2">otra</a>'
        sig = m.paginas_siguientes(m.parsear(html), CAT + "cardio")
        self.assertEqual(sig, [CAT + "cardio?page=2"])


class Actualizacion(unittest.TestCase):
    def sitio(self, cardio, mente, **kw):
        return FalsoFetcher({m.MAIN_URL: principal("cardio", "mente"),
                             CAT + "cardio": cardio, CAT + "mente": mente}, **kw)

    def test_agrega_nueva_y_quita_retirada_sin_tocar_las_demas(self):
        cardio = pagina_cat(("Pieza A", P + "a"), ("Titulo distinto", P + "b"), ("Pieza C", P + "c"),
                            ("Pieza NUEVA", P + "n"))  # D retirada
        nuevo, resumen = m.actualizar(catalogo(), self.sitio(cardio, pagina_cat(("Pieza M", P + "m"))),
                                      log=lambda *_: None)
        items = {i[1]: i for i in nuevo[0]["items"]}
        self.assertNotIn(P + "d", items)
        self.assertEqual(items[P + "n"], ["Pieza NUEVA", P + "n", "Cardio"])
        self.assertEqual(items[P + "b"][0], "Pieza B")  # conserva el título existente
        self.assertEqual(m.texto_resumen(resumen), "+1 / -1 piezas")

    def test_sin_cambios(self):
        cardio = pagina_cat(*[(t, u) for t, u, _ in catalogo()[0]["items"]])
        _, resumen = m.actualizar(catalogo(), self.sitio(cardio, pagina_cat(("Pieza M", P + "m"))),
                                  log=lambda *_: None)
        self.assertEqual(resumen, [])

    def test_lectura_rota_no_borra_la_categoria(self):
        nuevo, _ = m.actualizar(catalogo(), self.sitio("<html>cambió el diseño</html>",
                                                        pagina_cat(("Pieza M", P + "m"))), log=lambda *_: None)
        self.assertEqual(len(nuevo[0]["items"]), 4)

    def test_lectura_parcial_no_borra_la_categoria(self):
        nuevo, _ = m.actualizar(catalogo(), self.sitio(pagina_cat(("Pieza A", P + "a")),
                                                        pagina_cat(("Pieza M", P + "m"))), log=lambda *_: None)
        self.assertEqual(len(nuevo[0]["items"]), 4)

    def test_categoria_caida_se_conserva(self):
        nuevo, _ = m.actualizar(catalogo(), self.sitio(pagina_cat(("Pieza A", P + "a")), "",
                                                        caidas=[CAT + "mente"]), log=lambda *_: None)
        self.assertEqual(len(nuevo[1]["items"]), 1)

    def test_principal_sin_categorias_aborta(self):
        f = FalsoFetcher({m.MAIN_URL: "<html></html>"})
        with self.assertRaises(ValueError):
            m.actualizar(catalogo(), f, log=lambda *_: None)

    def test_categoria_nueva_se_agrega_con_icono_por_defecto(self):
        f = FalsoFetcher({m.MAIN_URL: principal("cardio", "mente", "nueva"),
                          CAT + "cardio": pagina_cat(*[(t, u) for t, u, _ in catalogo()[0]["items"]]),
                          CAT + "mente": pagina_cat(("Pieza M", P + "m")),
                          CAT + "nueva": pagina_cat(("Algo nuevo", P + "x"))})
        nuevo, _ = m.actualizar(catalogo(), f, log=lambda *_: None)
        self.assertEqual(nuevo[-1]["slug"], "nueva")
        self.assertEqual(nuevo[-1]["icon"], m.DEFAULT_ICON)

    def test_caida_total_grande_aborta(self):
        grande = [{"slug": "cardio", "nombre": "Cardio", "icon": "i", "color": "#000",
                   "items": [[f"P{i}", P + str(i), "t"] for i in range(10)]}]
        f = FalsoFetcher({m.MAIN_URL: principal("cardio"),
                          CAT + "cardio": pagina_cat(*[(f"P{i}", P + str(i)) for i in range(5)] )})
        # 5 de 10 = 50 %: pasa la salvaguarda por categoría, pero el total cae más de 20 %
        with self.assertRaises(ValueError):
            m.actualizar(grande, f, log=lambda *_: None)

    def test_menos_de_la_mitad_conserva_la_categoria(self):
        grande = [{"slug": "cardio", "nombre": "Cardio", "icon": "i", "color": "#000",
                   "items": [[f"P{i}", P + str(i), "t"] for i in range(10)]}]
        f = FalsoFetcher({m.MAIN_URL: principal("cardio"),
                          CAT + "cardio": pagina_cat(*[(f"P{i}", P + str(i)) for i in range(4)])})
        nuevo, resumen = m.actualizar(grande, f, log=lambda *_: None)
        self.assertEqual(len(nuevo[0]["items"]), 10)
        self.assertEqual(resumen, [])


class Bloque(unittest.TestCase):
    def test_index_real_ida_y_vuelta(self):
        html = (RAIZ / "index.html").read_text(encoding="utf-8")
        cats = m.leer_catalogo(html)
        self.assertEqual(m.escribir_catalogo(html, cats), html)
        self.assertGreaterEqual(len(cats), 21)

    def test_ejecutar_escribe_solo_el_bloque(self):
        html = (RAIZ / "index.html").read_text(encoding="utf-8")
        cats = m.leer_catalogo(html)
        paginas = {m.MAIN_URL: principal(*[c["slug"] for c in cats])}
        for c in cats:
            filas = [(i[0], i[1]) for i in c["items"]]
            if c["slug"] == cats[0]["slug"]:
                filas.append(("Pieza de prueba", P + "prueba"))
            paginas[CAT + c["slug"]] = pagina_cat(*filas)
        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / "index.html"
            ruta.write_text(html, encoding="utf-8")
            codigo, resumen = m.ejecutar(ruta, FalsoFetcher(paginas), log=lambda *_: None)
            nuevo = ruta.read_text(encoding="utf-8")
        self.assertEqual((codigo, resumen), (0, "+1 / -0 piezas"))
        self.assertIn("Pieza de prueba", nuevo)
        antes, despues = html.split(m.INICIO)[0], nuevo.split(m.INICIO)[0]
        self.assertEqual(antes, despues)  # nada fuera del bloque cambia
        self.assertEqual(html.split(m.FIN)[1], nuevo.split(m.FIN)[1])

    def test_dry_run_no_escribe(self):
        html = (RAIZ / "index.html").read_text(encoding="utf-8")
        cats = m.leer_catalogo(html)
        paginas = {m.MAIN_URL: principal(*[c["slug"] for c in cats])}
        for c in cats:
            paginas[CAT + c["slug"]] = pagina_cat(*[(i[0], i[1]) for i in c["items"]], ("Nueva", P + "nn"))
        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / "index.html"
            ruta.write_text(html, encoding="utf-8")
            m.ejecutar(ruta, FalsoFetcher(paginas), dry_run=True, log=lambda *_: None)
            self.assertEqual(ruta.read_text(encoding="utf-8"), html)


if __name__ == "__main__":
    unittest.main()
