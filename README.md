# materiales-educativos

Materiales educativos interactivos.

## Piezas educativas HIC (`index.html`)

Buscador del catálogo de piezas educativas del HIC. El catálogo vive dentro de
`index.html`, entre los marcadores `CATALOGO:INICIO` y `CATALOGO:FIN`, y se
mantiene solo.

### Actualización automática

El flujo `.github/workflows/actualizar-catalogo.yml` corre todos los días a las
6:00 a. m. (hora de Colombia) y también se puede lanzar a mano desde la pestaña
**Actions → Actualizar catálogo de piezas → Run workflow**.

Ejecuta `scripts/actualizar_catalogo.py`, que:

- lee la página de educación al paciente de hic.fcv.org y cada categoría;
- agrega las piezas nuevas y quita las retiradas; las que ya estaban no se tocan;
- hace commit en `main` solo si hubo cambios.

Salvaguardas: respeta el `robots.txt` del sitio, solo acepta enlaces de
`hic.fcv.org`, y si algo se ve raro (cambió el diseño del sitio, una categoría
queda casi vacía o el total cae más de 20 %) no modifica nada y el flujo avisa
con un error.

Las piezas nuevas entran con el nombre de su categoría como etiqueta. Una
categoría nueva entra con ícono y color por defecto, y se puede ajustar a mano
en el bloque del catálogo.

### Uso local

```bash
python scripts/actualizar_catalogo.py --dry-run   # muestra qué cambiaría
python scripts/actualizar_catalogo.py             # actualiza index.html
python -m unittest discover -s scripts            # pruebas
```
