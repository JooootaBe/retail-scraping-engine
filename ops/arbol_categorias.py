#!/usr/bin/env python3
"""
Vigilancia del árbol de categorías de Makro Perú.

QUÉ RESPONDE
------------
"¿Makro abrió una línea nueva?" Hoy no hay forma de enterarse. El motor
recorre el árbol en cada corrida y lo usa, pero nunca lo COMPARA contra el de
ayer: una categoría nueva entra al recorrido en silencio y una que desaparece
sale igual de callada.

Una categoría nueva no es ruido técnico, es señal comercial — alguien del otro
lado decidió empezar a vender algo. Este script existe para que esa decisión
sea visible el día que ocurre y no seis meses después.

QUÉ NO ES
---------
NO es parte del motor. No mide precios, no escribe filas, no toca `src/`.
Es una utilidad operativa: se corre a mano o por cron, y lo único que produce
es un snapshot fechado y un diff en consola.

Vive en `ops/` justamente para que esa frontera se vea en el árbol de
directorios. Si algún día necesita entrar al motor, será porque el motor
necesita reaccionar al cambio, y eso es otra decisión.

QUÉ REUSA DEL MOTOR (sin tocarlo)
---------------------------------
    aplanar_categorias      la ruta completa desde la raíz, que es la clave
                            de todo el diff
    total_desde_resources   leer el total de la cabecera `resources`,
                            distinguiendo "no sé" de "sé que es cero"
    Cliente                 intervalo mínimo, backoff, tope duro
    abrir_navegador         mismo canal y mismo fallback que el motor
    BASE_URL / SALES_CHANNEL

Reimplementar cualquiera de esas sería garantizar que un día divergen y que
este script vigile un árbol distinto del que el motor recorre.

USO
---
    python3 ops/arbol_categorias.py                # 1 request, segundos
    python3 ops/arbol_categorias.py --contar       # 1 request por categoría
    python3 ops/arbol_categorias.py --contar --tope 200
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from retail_engine.collectors.makro_plazavea import (
    BASE_URL,
    SALES_CHANNEL,
    Cliente,
    TopeAgotadoError,
    abrir_navegador,
    aplanar_categorias,
    raiz_repo,
    total_desde_resources,
)

# Misma profundidad que pide el motor. Si acá se pidiera otra, este script
# vigilaría un árbol que el motor nunca ve, y un "NUEVA" podría ser una
# categoría que la extracción jamás va a recorrer.
PROFUNDIDAD_ARBOL = 3

# El motor no baja de acá y este script tampoco: el servidor no es nuestro.
INTERVALO_MINIMO = 1.5

SALIDA = raiz_repo() / "data" / "arbol"

# Umbral del salto de volumen. DOS condiciones, y hay que pasar las dos.
#
# Solo relativo: una categoría de 4 SKUs que pasa a 6 es un +50% que no
# significa nada y aparecería en cada corrida.
# Solo absoluto: +50 SKUs sobre una categoría de 3.000 es ruido de inventario.
# Juntos dejan pasar lo que el usuario quiere ver — 3.000 -> 4.200 — y callan
# la respiración normal del catálogo.
SALTO_RELATIVO = 0.20
SALTO_ABSOLUTO = 50


def log(mensaje: str = "") -> None:
    print(mensaje, flush=True)


# ===========================================================================
# SNAPSHOTS
# ===========================================================================


def ruta_snapshot(momento: datetime) -> Path:
    return SALIDA / f"arbol_{momento.strftime('%Y%m%d')}.json"


def snapshot_previo() -> Path | None:
    """
    El snapshot más reciente que YA existe, incluido el de hoy.

    Incluir el de hoy es deliberado: correr el script dos veces seguidas tiene
    que decir "sin cambios", no "no hay con qué comparar". Si excluyera la
    fecha de hoy, la segunda corrida del día se compararía contra ayer y
    repetiría un diff que ya se reportó.
    """

    if not SALIDA.exists():
        return None

    previos = sorted(SALIDA.glob("arbol_*.json"))

    return previos[-1] if previos else None


def leer_snapshot(ruta: Path) -> dict[str, Any]:
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"No se pudo leer {ruta}: {type(exc).__name__}: {exc}")

    if not isinstance(datos, dict) or not isinstance(datos.get("categorias"), list):
        raise SystemExit(f"{ruta} no tiene la forma esperada de un snapshot.")

    return datos


def escribir_snapshot(categorias: list[dict], con_conteo: bool,
                      momento: datetime) -> Path:
    SALIDA.mkdir(parents=True, exist_ok=True)

    destino = ruta_snapshot(momento)

    destino.write_text(
        json.dumps(
            {
                "generado": momento.isoformat(timespec="seconds"),
                "fuente": f"{BASE_URL}/api/catalog_system/pub/category/tree/"
                          f"{PROFUNDIDAD_ARBOL}",
                "sales_channel": SALES_CHANNEL,
                "profundidad": PROFUNDIDAD_ARBOL,
                # Sin esta bandera, un snapshot sin conteos y uno con conteos
                # en cero serían indistinguibles, y el diff de volumen
                # reportaría que todo el catálogo se vació.
                "con_conteo": con_conteo,
                "categorias": categorias,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return destino


# ===========================================================================
# DIFF — funciones puras
# ===========================================================================


def indexar(categorias: list[dict]) -> dict[str, dict]:
    """Por `id`, que es lo único estable: nombre y ruta son justo lo que cambia."""

    return {str(c.get("id")): c for c in categorias if c.get("id")}


def comparar(viejas: list[dict], nuevas: list[dict]) -> dict[str, list]:
    """
    Diff entre dos snapshots. Devuelve listas, no imprime.

    La clave es el `id`. El nombre no sirve —renombrar es uno de los cambios
    que se busca— y la ruta tampoco: mover una categoría de padre le cambia la
    ruta sin que deje de ser la misma categoría.
    """

    antes = indexar(viejas)
    ahora = indexar(nuevas)

    resultado: dict[str, list] = {
        "nuevas": [ahora[i] for i in ahora if i not in antes],
        "desaparecidas": [antes[i] for i in antes if i not in ahora],
        "renombradas": [],
        "movidas": [],
        "saltos": [],
    }

    for id_categoria in ahora:
        if id_categoria not in antes:
            continue

        vieja, nueva = antes[id_categoria], ahora[id_categoria]

        if vieja.get("name") != nueva.get("name"):
            resultado["renombradas"].append((vieja, nueva))

        # Misma categoría colgando de otro padre. No estaba en el encargo
        # original, pero sin esto un re-parenting no aparece en NINGUNA de las
        # tres listas: mismo id, mismo nombre, ruta distinta — el diff diría
        # "sin cambios" sobre un árbol que se reorganizó.
        if vieja.get("ruta") != nueva.get("ruta"):
            resultado["movidas"].append((vieja, nueva))

    return resultado


def es_salto(antes: Any, ahora: Any) -> bool:
    """Cambio de volumen que merece mirarse. Ver SALTO_RELATIVO/ABSOLUTO."""

    if not isinstance(antes, int) or not isinstance(ahora, int):
        return False

    diferencia = abs(ahora - antes)

    if diferencia < SALTO_ABSOLUTO:
        return False

    # Una categoría que nace con stock (antes 0) siempre es noticia.
    if antes <= 0:
        return True

    return diferencia / antes >= SALTO_RELATIVO


def comparar_volumen(viejas: list[dict], nuevas: list[dict]) -> list[tuple]:
    """Saltos de conteo entre dos snapshots que AMBOS tengan conteo."""

    antes = indexar(viejas)
    saltos = []

    for id_categoria, nueva in indexar(nuevas).items():
        vieja = antes.get(id_categoria)

        if not vieja:
            continue

        if es_salto(vieja.get("skus"), nueva.get("skus")):
            saltos.append((vieja, nueva))

    return saltos


# ===========================================================================
# RED
# ===========================================================================


async def descargar(argumentos) -> tuple[list[dict], bool]:
    """Devuelve `(categorias, se_contaron_skus)`."""

    from playwright.async_api import async_playwright

    intervalo = max(INTERVALO_MINIMO, argumentos.intervalo)

    if argumentos.intervalo < INTERVALO_MINIMO:
        log(f"AVISO: --intervalo {argumentos.intervalo} es menor que el mínimo "
            f"de {INTERVALO_MINIMO}s. Se usa {intervalo}s.")

    async with async_playwright() as playwright:
        navegador = await abrir_navegador(
            playwright, argumentos.canal, not argumentos.headed
        )

        try:
            contexto = await navegador.new_context(
                locale="es-PE", timezone_id="America/Lima"
            )

            cliente = Cliente(
                contexto.request,
                intervalo=intervalo,
                reintentos=argumentos.reintentos,
                tope=argumentos.tope or 5000,
            )
            cliente.fase = "arbol"

            log("Descargando el árbol de categorías...")

            status, arbol, _ = await cliente.pedir(
                f"{BASE_URL}/api/catalog_system/pub/category/tree/{PROFUNDIDAD_ARBOL}"
            )

            if status >= 400 or not isinstance(arbol, list):
                raise SystemExit(
                    f"El árbol respondió HTTP {status}. Sin árbol no hay nada "
                    "que comparar: no se escribe snapshot."
                )

            categorias = [dict(c) for c in aplanar_categorias(arbol)]

            log(f"  {len(categorias)} categorías (profundidad {PROFUNDIDAD_ARBOL}).")

            if not argumentos.contar:
                return categorias, False

            # ---- conteo de SKUs: 1 request por categoría ----
            cliente.fase = "conteo"

            if not argumentos.tope:
                cliente.tope = len(categorias) + 10

            minutos = len(categorias) * intervalo / 60

            log("")
            log(f"Contando SKUs: {len(categorias)} requests a {intervalo}s "
                f"≈ {minutos:.0f} min. Ctrl-C corta y NO escribe snapshot.")
            log("")

            for numero, categoria in enumerate(categorias, 1):
                url = (
                    f"{BASE_URL}/api/catalog_system/pub/products/search"
                    f"?fq=C:/{categoria['ruta']}/&_from=0&_to=0"
                    f"&sc={SALES_CHANNEL}"
                )

                try:
                    status, _datos, cabeceras = await cliente.pedir(url)
                except TopeAgotadoError as exc:
                    # Agotar el tope NO es "esta categoría no respondió": es
                    # que no queda presupuesto para ninguna. Sin este corte, el
                    # `except Exception` de abajo marcaría las 3.000 restantes
                    # con conteo None y el snapshot se guardaría como si el
                    # conteo estuviera hecho — un archivo que la próxima
                    # corrida leería como catálogo vaciado.
                    raise SystemExit(
                        f"Presupuesto agotado en la categoría {numero} de "
                        f"{len(categorias)} ({exc}). No se escribe snapshot: "
                        "un conteo a medias es peor que ninguno. Subí --tope "
                        f"a por lo menos {len(categorias) + 10}."
                    )
                except Exception as exc:
                    # Una categoría que no responde deja su conteo en None, que
                    # es distinto de cero: `es_salto` ignora los None en vez de
                    # reportar que la categoría se vació.
                    categoria["skus"] = None
                    log(f"  [{numero}/{len(categorias)}] {categoria['name'][:40]:<42}"
                        f" ✗ {type(exc).__name__}")
                    continue

                categoria["skus"] = (
                    total_desde_resources(cabeceras.get("resources", ""))
                    if status < 400
                    else None
                )

                if numero % 25 == 0 or numero == len(categorias):
                    log(f"  [{numero}/{len(categorias)}] "
                        f"{categoria['name'][:40]:<42} {categoria['skus']}")

            return categorias, True

        finally:
            await navegador.close()


# ===========================================================================
# REPORTE
# ===========================================================================


def describir(categoria: dict) -> str:
    total = categoria.get("skus")
    sufijo = f"  [{total} SKUs]" if isinstance(total, int) else ""

    return f"{categoria.get('id'):>7}  /{categoria.get('ruta')}/  {categoria.get('name')}{sufijo}"


def reportar_listado(categorias: list[dict]) -> None:
    log("")
    log("ÁRBOL ACTUAL")
    log("-" * 100)

    for categoria in sorted(categorias, key=lambda c: c["ruta"]):
        sangria = "  " * (int(categoria.get("nivel", 1)) - 1)
        total = categoria.get("skus")
        sufijo = f"  [{total} SKUs]" if isinstance(total, int) else ""

        log(f"  {categoria['id']:>7}  {sangria}{categoria['name']}{sufijo}")


def reportar_diff(diff: dict[str, list], saltos: list[tuple],
                  volumen_comparable: bool) -> bool:
    """Imprime el diff. Devuelve si hubo algún cambio."""

    log("")
    log("CAMBIOS RESPECTO DEL SNAPSHOT ANTERIOR")
    log("-" * 100)

    hubo = False

    for categoria in sorted(diff["nuevas"], key=lambda c: c["ruta"]):
        hubo = True
        log(f"  + NUEVA          {describir(categoria)}")

    for categoria in sorted(diff["desaparecidas"], key=lambda c: c["ruta"]):
        hubo = True
        log(f"  - DESAPARECIDA   {describir(categoria)}")

    for vieja, nueva in sorted(diff["renombradas"], key=lambda p: p[1]["ruta"]):
        hubo = True
        log(f"  ~ RENOMBRADA     {nueva['id']:>7}  /{nueva['ruta']}/  "
            f"{vieja['name']} → {nueva['name']}")

    for vieja, nueva in sorted(diff["movidas"], key=lambda p: p[1]["ruta"]):
        hubo = True
        log(f"  ~ MOVIDA         {nueva['id']:>7}  {nueva['name']}  "
            f"/{vieja['ruta']}/ → /{nueva['ruta']}/")

    if volumen_comparable:
        for vieja, nueva in sorted(saltos, key=lambda p: p[1]["ruta"]):
            hubo = True
            antes, ahora = vieja.get("skus"), nueva.get("skus")
            signo = "+" if ahora > antes else ""
            log(f"  ↕ VOLUMEN        {nueva['id']:>7}  /{nueva['ruta']}/  "
                f"{nueva['name']}: {antes} → {ahora} "
                f"({signo}{ahora - antes})")

    if not hubo:
        log("  sin cambios")

    if not volumen_comparable:
        log("")
        log("  (saltos de volumen no comparados: hace falta --contar en ESTA "
            "corrida y en la anterior)")

    return hubo


# ===========================================================================
# CLI
# ===========================================================================


def parsear_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Descarga el árbol de categorías de Makro, lo guarda con fecha y "
            "lo compara contra el snapshot anterior. Utilidad operativa: no "
            "es parte del motor y no mide precios."
        )
    )

    parser.add_argument(
        "--contar", action="store_true",
        help=(
            "Además del árbol, cuenta los SKUs de cada categoría. Cuesta UN "
            "REQUEST POR CATEGORÍA (~3.400 hoy, ~85 min a 1.5s), por eso es "
            "opt-in. Habilita el reporte de saltos de volumen, que necesita "
            "conteo en esta corrida y en la anterior."
        ))

    parser.add_argument(
        "--intervalo", type=float, default=INTERVALO_MINIMO,
        help=f"Segundos entre requests. Mínimo {INTERVALO_MINIMO}. Default: "
             f"{INTERVALO_MINIMO}")

    parser.add_argument(
        "--tope", type=int, default=0,
        help="Máximo de requests. 0 = automático (una por categoría + margen).")

    parser.add_argument("--reintentos", type=int, default=3,
                        help="Reintentos ante 429/5xx. Default: 3")

    parser.add_argument("--canal", default="chrome",
                        help="Canal Playwright: chrome | msedge | chromium.")

    parser.add_argument("--headed", action="store_true",
                        help="Muestra el navegador.")

    parser.add_argument("--sin-guardar", action="store_true", dest="sin_guardar",
                        help="Compara e imprime, no escribe el snapshot.")

    return parser.parse_args()


async def principal() -> int:
    argumentos = parsear_argumentos()
    momento = datetime.now()

    log("=" * 100)
    log("ÁRBOL DE CATEGORÍAS — MAKRO PERÚ")
    log("=" * 100)
    log(f"Snapshots  : {SALIDA}")
    log(f"Conteo     : {'sí (1 request por categoría)' if argumentos.contar else 'no (--contar lo activa)'}")

    # El previo se resuelve ANTES de escribir: si no, la corrida se compararía
    # contra sí misma y siempre diría "sin cambios".
    previo = snapshot_previo()

    log(f"Anterior   : {previo.name if previo else 'no hay snapshot previo'}")
    log("=" * 100)

    categorias, con_conteo = await descargar(argumentos)

    if previo is None:
        reportar_listado(categorias)
        log("")
        log("No hay snapshot previo: esta corrida es la línea de base. "
            "La próxima ya puede comparar.")
        hubo_cambios = False
    else:
        datos_previos = leer_snapshot(previo)
        viejas = datos_previos["categorias"]

        diff = comparar(viejas, categorias)

        volumen_comparable = bool(con_conteo and datos_previos.get("con_conteo"))
        saltos = comparar_volumen(viejas, categorias) if volumen_comparable else []

        log("")
        log(f"Comparando {len(viejas)} categorías de {previo.name} "
            f"contra {len(categorias)} de hoy.")

        hubo_cambios = reportar_diff(diff, saltos, volumen_comparable)

    log("")

    if argumentos.sin_guardar:
        log("--sin-guardar: no se escribió snapshot.")
    else:
        destino = escribir_snapshot(categorias, con_conteo, momento)
        log(f"Snapshot: {destino}")

    log("=" * 100)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(principal()))
    except ModuleNotFoundError as exc:
        log(f"Falta una dependencia: {exc}. Instalar con `pip install -e .` "
            "y `playwright install chromium`.")
        sys.exit(2)
    except KeyboardInterrupt:
        log("\nInterrumpido. No se escribió snapshot.")
        sys.exit(130)
