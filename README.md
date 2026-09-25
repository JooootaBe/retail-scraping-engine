# retail-scraping-engine

Recopilación de datos de inteligencia de precios para el comercio peruano. Consulta las API públicas de las tiendas online de un minorista o mayorista y registra una fila por cada SKU, sucursal y ejecución, lo que permite a un analista de precios crear una serie temporal y comparar las sucursales posteriormente.

El objetivo es **una atribución de sucursal fiable**: cada fila registra qué sucursal respondió realmente, y con qué grado de certeza lo hace el motor. Correctitud antes que cobertura.

## Estado

| Colector | Minorista | Estado |
|---|---|---|
| `makro_plazavea` | Makro Perú (tienda VTEX) | funcionando — 2 sucursales: 359 Santa Anita, 360 Surco |
| `makro_pe` | Makro Perú, otra fuente | sin empezar, y deliberadamente |

Versión del paquete **1.2.0**; versión del motor `2026.08.28-24`, `SCHEMA_VERSION 7`, 79 columnas por fila. La 1.1.0 entregó las columnas mayoristas (bi-precio) y el esquema de una carpeta por ejecución; la 1.2.0 agregó `--categoria`. Desde entonces hay siete versiones del motor — de la v18 a la v24 — **liberadas en git pero no etiquetadas como versión de paquete**; tres de ellas mueven `SCHEMA_VERSION` (4 → 5 en v18, 5 → 6 en v20, 6 → 7 en v24), así que lee la sección `[Sin publicar]` de `CHANGELOG.md` antes de consolidar ejecuciones de fechas distintas.

Las dos sucursales son una decisión, no una limitación: la atribución de sucursal se prueba correcta sobre un conjunto pequeño de nodos antes de agregar más. `CLAUDE.md` explica por qué, y cuánto cuesta agregar la número 21.

## Instalación

```bash
python -m pip install -e .     # Python >=3.12, instala playwright>=1.62.0
playwright install chromium
```

## Ejecución

```bash
python3 src/retail_engine/collectors/makro_plazavea.py --catalogo 300 --por-categoria 3 --muestra 100
```

Eso descubre hasta 300 SKUs, limita cada subcategoría a 3, y mide 100 de ellos contra cada sucursal configurada. `--version` imprime la versión del motor y su registro de cambios sin tocar la red. La lista completa de flags está en `CLAUDE.md`.

La salida cae en `data/makro_plazavea/run_<YYYYMMDD_HHMMSS>/` — una carpeta inmutable por ejecución, que contiene `filas.csv`, `run.json` y `raw.jsonl.gz`. Leer toda la historia es un glob sobre `run_*/filas.csv`. El código de salida del proceso es significativo: 0 completa, 1 la ejecución entregó menos de lo que prometía su selección, 2 argumentos inválidos o Playwright ausente, 130 Ctrl-C.

### La ejecución diaria es automática

Desde el 2026-09-06 la serie no depende de que alguien se acuerde: un timer de usuario de systemd dispara `ops/corrida_diaria.sh` todos los días a las 02:00 hora de Lima. Medido el 2026-09-24, lleva **19 días consecutivos sin un hueco**, todos completos y con código de salida 0.

```bash
ops/corrida_diaria.sh                            # la misma ejecución, a mano, ahora
systemctl --user list-timers motor-makro.timer   # cuándo dispara la próxima
journalctl --user -u motor-makro -n 100          # su consola
```

El alcance diario vive en una sola constante (`ALCANCE_DIARIO`) dentro del envoltorio, y cambiarlo cambia qué universo mide la serie de ahí en adelante. Las unidades están versionadas en `ops/systemd/` y enlazadas a `~/.config/systemd/user/`, así que el repositorio es la fuente. `CLAUDE.md` explica por qué cada valor de las unidades es el que es — ninguno es una preferencia.

## Pruebas

64 funciones `test_` repartidas en seis archivos bajo `tests/makro_plazavea/`, ninguna de las cuales toca la red. Pytest no es una dependencia declarada; cada archivo también corre de forma independiente y sale con 0/1:

```bash
python3 tests/makro_plazavea/test_precio_mayorista.py
python -m pytest tests/ -q                              # si pytest está instalado
```

Cada archivo resuelve la raíz del repositorio como `parents[2]` desde su propia ruta, así que la profundidad `tests/<colector>/<archivo>.py` es estructural: movido un nivel más abajo, la suite muere con `ModuleNotFoundError`. Las cinco sondas exploratorias que encontraron el bi-precio están archivadas en `tests/historia/sondas_makro_plazavea/` y no corren — una sonda no es una prueba de regresión, y las dos no son intercambiables.

## Estructura

```
src/retail_engine/collectors/makro_plazavea.py   el motor
ops/                                             herramientas operativas; nunca miden precios
ops/corrida_diaria.sh, ops/systemd/              la ejecución diaria automática de las 02:00
docs/columnas.md                                 vivo: el diccionario de las 79 columnas
docs/historia/                                   archivado: el registro cerrado de la era 1.1.0
docs/bodegueros/                                 el nuevo rumbo (Los Bodegueros)
tests/makro_plazavea/                            la suite de regresión — seis archivos, todos vivos
tests/fixtures/makro_plazavea/                   línea base golden + 23 fichas de tienda capturadas a mano
tests/historia/sondas_makro_plazavea/            archivado: cinco sondas exploratorias, no corren
tests/historia/regresion_makro_plazavea/         archivado: una prueba de regresión cuya ejecución fue borrada
data/                                            salida generada (gitignored)
```

## Dónde está documentado cada cosa

- `CLAUDE.md` — principios rectores, arquitectura, y cómo ejecutar y verificar un cambio. Léelo antes de tocar la lógica de extracción. Es el único documento mantenido al día por diseño.
- `CHANGELOG.md` — historial de versiones, más `[Sin publicar]` para v18–v24.
- `docs/columnas.md` — el diccionario de columnas: qué dice cada una de las 79 columnas de `filas.csv`, por qué existe, qué significa su celda vacía, y cuáles cambiaron de significado entre versiones de esquema.
- `docs/historia/` — registro archivado de la era 1.1.0. Nada de ahí es una hoja de ruta; se conserva porque contiene la evidencia que forzó cada cambio. Ver `docs/historia/LEEME.md`.
  - `decisiones_1.1.0.md` — inventario cerrado de lo que entregó la 1.1.0. Partes fueron superadas por versiones posteriores y están anotadas en su lugar. Aún se cita desde `CLAUDE.md` y `docs/columnas.md`.
  - `brief_*.md` — tres órdenes de trabajo cerradas.
  - `contradicciones.md` — la auditoría de documentación previa a la 1.1.0, con sus resoluciones.
- `docs/bodegueros/` — el nuevo rumbo. Ver `docs/bodegueros/README.md`.

## Licencia

Privada. No licenciada para su redistribución.
