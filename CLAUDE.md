# CLAUDE.md

Este archivo le da orientación a Claude Code (claude.ai/code) cuando trabaja con el código de este repositorio.

## Misión — leer esto antes de tocar la lógica de extracción

Esto no es un scraper genérico. Es el motor de recolección de datos de una **práctica de inteligencia de
precios**: cada decisión de diseño existe para que un analista de pricing pueda confiar en la atribución de
sucursal de una fila lo suficiente como para construir una estrategia encima. Hoy el motor sigue dos
sucursales de Makro Perú (359 Santa Anita, 360 Surco), y eso es deliberado — **Nivel 1: Correctitud** va
antes que **Nivel 2: Cobertura**. Ese principio se aprendió a través de dos reescrituras fallidas de este
motor; este archivo es hoy el único lugar donde sobrevive ese registro. Probá que la resolución de sucursal
es hermética sobre un conjunto chico de nodos bien entendidos antes de agregar más.

Cada cambio se juzga contra esta pregunta: **¿hace más correcta la atribución de sucursal, o solo hace más
grande el dataset?** Un dataset más grande con atribución difusa es peor que uno chico en el que se puede
confiar. Nunca agregues una sucursal más rápido de lo que podés verificar que su firma es real (ver
"Escalar a 20+ sucursales").

## Qué es esto

Un extractor de propósito único: saca precios de retail por sucursal del storefront de Makro Perú, montado
sobre VTEX (`www.makro.plazavea.com.pe`), usando el `APIRequestContext` asíncrono de Playwright para llamar
directo a las APIs públicas `simulation` / `orderForm` de VTEX (sin scraping de páginas, sin compras). La
lógica de extracción sigue siendo **un solo archivo**: un módulo dentro de un paquete instalable. Seis
archivos de regresión cubren las reglas puras — bajo pytest o de forma independiente, ninguno toca la red —
así que son la primera verificación de un cambio, y nunca la última.

```
src/retail_engine/collectors/makro_plazavea.py   el motor (VERSION 2026.08.28-24, SCHEMA_VERSION 7, 79 columnas)
pyproject.toml                                   paquete `retail-engine` 1.2.0, Python >=3.12, playwright>=1.62.0
CHANGELOG.md                                     cambios publicados y sin publicar
docs/columnas.md                                 el diccionario de columnas — qué dice cada una de las 79
docs/historia/decisiones_1.1.0.md                inventario cerrado de lo que entregó 1.1.0 — archivado, aún citado
docs/historia/brief_*.md                         tres órdenes de trabajo cerradas — historia, no hoja de ruta
docs/historia/contradicciones.md                 la auditoría de documentación previa a 1.1.0 y sus resoluciones
docs/bodegueros/                                 el rumbo nuevo (Los Bodegueros) — su propio espacio
tests/makro_plazavea/                            la suite de regresión — permanente, seis archivos, todos vivos
tests/makro_plazavea/test_precio_mayorista.py    23 fichas del storefront — la única verdad EXTERNA del repo
tests/makro_plazavea/test_precio_en_quiebre.py   las seis ramas de price_origin, filas armadas a mano
tests/makro_plazavea/test_teaser_de_tarjeta.py   el teaser de tarjeta: régimen y descuento, payloads textuales
tests/makro_plazavea/test_truncamiento.py        los cuatro casos del techo de paginación
tests/makro_plazavea/test_auditoria_mayorista.py la auditoría mayorista: expectativa, propagación, estratos
tests/makro_plazavea/test_estado_por_corrida.py  los globales por corrida sobreviven a su propio reset
tests/historia/sondas_makro_plazavea/            cinco sondas exploratorias (v1…v5) — archivadas, no corren
tests/historia/regresion_makro_plazavea/         test_propiedades_corrida.py — archivado, su corrida ya no está
tests/fixtures/makro_plazavea/golden_v5.csv      línea base producida por la sonda v5, con su propio README
tests/fixtures/makro_plazavea/fichas_publicadas_20260822.csv   las 23 fichas capturadas a mano que lee test_precio_mayorista.py
ops/                                             herramientas operativas — NO el motor; nunca miden precios ellas mismas
ops/arbol_categorias.py                          fotografía el árbol de categorías y lo difea contra el anterior
ops/obtener_nodo_logistico_mk.py                 captura la firma logística viva de una sucursal (headed, independiente)
ops/corrida_diaria.sh                            el envoltorio de la corrida diaria — el comando canónico, a mano o por timer
ops/alerta_fallo.sh                              avisa (escritorio + `logs/fallas.log`) cuando la diaria agotó su reintento
ops/systemd/                                     las tres unidades de usuario que disparan la diaria a las 02:00
data/                                            salida generada (gitignored, `.gitignore:35`)
```

`ops/` es una frontera deliberada, no una carpeta para sobras. Lo que está ahí corre a mano o por timer, nunca
modifica el motor y nunca es importado *por* él. Ojo que el contrato es de una sola dirección: una herramienta
de ops puede importar el motor, pero no está obligada.

Hay dos clases adentro, y conviene no confundirlas. Las **herramientas de pregunta** responden algo *sobre* el
catálogo o una sucursal en vez de extraer precios (`arbol_categorias.py`, `obtener_nodo_logistico_mk.py`) — ésa
era la definición entera de `ops/` hasta que se automatizó la corrida. Las de **infraestructura de ejecución**
(`corrida_diaria.sh`, `alerta_fallo.sh`, `systemd/`) no responden ninguna pregunta: deciden *cuándo* y *bajo
qué entorno* corre el motor. Siguen sin medir precios ellas mismas —invocan al CLI y no lo reimplementan— y por
eso la regla de oro de la carpeta aguanta: **si un archivo de `ops/` alguna vez parsea una respuesta de VTEX o
escribe una fila, está en la carpeta equivocada.**

`arbol_categorias.py` sí importa el motor, y existe porque una categoría nueva es una señal comercial —alguien
del otro lado decidió empezar a vender algo— y hasta ahora entraba al recorrido en silencio.
`obtener_nodo_logistico_mk.py` no importa nada de `retail_engine` (solo Playwright, headed): captura la firma
logística real de una sucursal desde un checkout vivo, que es la evidencia que una entrada nueva de `NODOS`
necesita *antes* de ser confiable — ver "Escalar a 20+ sucursales", donde el costo de una firma equivocada es
una sucursal que parece cubierta y aporta cero precios verificados. Su salida, `ops/captura_mk_shipping.json`,
está **gitignoreada y tiene que quedarse así**: el `post_data` que la herramienta conserva a propósito —es
justamente lo que se quería estudiar— trae adentro los identificadores de sesión que el storefront embute en el
cuerpo (`VtexRCSessionIdv7`, `retailer-visitor-id`), así que el filtro de cabeceras del script no alcanza. El
motor nunca escribe tokens al disco; una captura de ops sí puede, y por eso no se commitea.

El proyecto existe para construir una serie temporal de precios por SKU y por sucursal, de modo que un analista
de pricing pueda comparar sucursales después en SQL/pandas. **El extractor mismo nunca compara sucursales y
nunca descarta filas** — leé "Principios rectores" antes de cambiar la lógica de extracción.

## Cómo ejecutarlo

```bash
python -m pip install -e .
playwright install chromium          # el --canal por defecto es "chrome"; cae al Chromium gestionado

MK=src/retail_engine/collectors/makro_plazavea.py

python3 $MK --version                                        # versión + changelog, sin red
python3 $MK --catalogo 300 --por-categoria 3 --muestra 100   # corrida de prueba normal
python3 $MK --tope 60000                                     # catálogo completo (lento, deliberado)
python3 $MK --modo orderform                                 # flujo de 3 requests en vez de simulation
python3 $MK --categoria "/399/"                              # solo esa rama del árbol
python3 $MK --skus 10012716,11401644                         # mide una lista explícita, sin descubrimiento
python3 $MK --dry-run --skus 10012716                        # mide, imprime, no escribe nada
python3 $MK --salida /otra/ruta                              # escribe en otro lado
```

Los flags que importan: `--catalogo N` (corta el descubrimiento en N SKUs), `--muestra N` (cuántos de los SKUs
descubiertos se miden; selección determinista por hash), `--por-categoria N` (máximo de SKUs por subcategoría —
hace que la muestra sea *ancha* en vez de las primeras dos categorías del árbol), `--semilla` (sal del hash de
selección), `--presupuesto-descubrimiento N` (presupuesto de requests reservado solo para descubrimiento),
`--tope N` (tope duro de requests por corrida; 0 = automático), `--intervalo` (segundos entre requests, 1.5 por
defecto), `--reintentos` (reintentos ante 429/5xx, 3 por defecto), `--auditoria PCT` (% de mediciones
contrastadas simulation vs orderForm, 5 por defecto, 0 lo desactiva), `--sin-evidencia` (no archiva el JSON
crudo), `--modo simulation|orderform` (1 request contra el flujo de 3), `--salida` (raíz del colector; la
carpeta de la corrida se sigue creando adentro), `--version` (imprime `VERSION` + `CAMBIOS` y sale, sin red),
`--headed` / `--canal`. Con `--categoria`, `--skus`, `--dry-run` y `--auditoria-mayorista` de más abajo, ésa es
toda la superficie de argparse — 19 flags. Si agregás uno, va en este párrafo o en alguno de abajo, no solo en
el `--help`.

1.2.0 agregó `--categoria "/399/,/77/"` — ver "Principio rector: el alcance se elige, no se hereda del árbol"
más abajo; es el flag con más razonamiento detrás.

Tres flags llegaron con 1.1.0. `--skus <lista>` (máximo 20) mide una lista explícita y saltea el descubrimiento
— es incompatible con `--catalogo` / `--muestra` a propósito, porque una corrida tiene que tener exactamente
una respuesta a "cómo se seleccionó esto"; el manifiesto registra `modo_seleccion` para poder filtrar después
estas corridas de la serie. `--dry-run` mide e imprime sin escribir nada, ni siquiera la carpeta de la corrida.
`--auditoria-mayorista N` (3 por defecto, 0 lo desactiva) es el único lugar donde el motor pide `qty > 1` — ver
"Principio rector: el precio mayorista se reconstruye, no se observa".

**`--reiniciar` ya no existe.** Borraba los CSVs por sucursal para "reiniciar la serie", lo que tenía sentido
cuando la serie era un solo archivo mutable al que se le hacía append. Con una carpeta inmutable por corrida no
hay archivo acumulado que reiniciar, y lo único que el flag todavía podría borrar es historia cerrada. Un flag
cuyo único efecto posible es destruir el pasado no se redefine — se saca.

### La corrida diaria es automática (02:00, timer de systemd de usuario)

Desde el 2026-09-06 la serie no depende de que alguien se acuerde. Un timer de usuario dispara
`ops/corrida_diaria.sh` todos los días a las 02:00 hora de Lima. Antes de eso las corridas se lanzaban a mano,
y la serie ya llevaba las dos cicatrices: **el 2026-08-28 no tiene corrida** y `run_20260829_020141` arrancó
tarde. Las dos son las que la automatización existe para no repetir.

```bash
systemctl --user list-timers motor-makro.timer   # cuándo dispara la próxima
systemctl --user status motor-makro.service      # cómo terminó la última
journalctl --user -u motor-makro -n 100          # su consola
ops/corrida_diaria.sh                            # la misma corrida, a mano, ahora
```

El alcance diario (`--categoria "/431/" --auditoria-mayorista 10`) vive en **una sola constante**,
`ALCANCE_DIARIO` dentro de `corrida_diaria.sh`. Cambiarlo cambia qué universo mide la serie de ahí en adelante,
así que las corridas viejas y las nuevas dejan de ser comparables sin filtrar por fecha — leé "el alcance se
elige, no se hereda del árbol" antes de tocarlo. El script sin argumentos usa ese alcance; **con** argumentos
los pasa tal cual al motor, que es lo que permite probar el envoltorio en 9 segundos
(`ops/corrida_diaria.sh --dry-run --skus 10012716`) en vez de esperar 1,6 h.

Cuatro decisiones están codificadas en las unidades y cada una responde a algo medido, no a una preferencia:

- **`Persistent=true`.** Si a las 02:00 la máquina estaba apagada o suspendida, la corrida arranca apenas
  prende. El `run_id` lleva entonces la hora real de arranque, no las 02:00 — un día tardío sigue siendo un día
  en la serie, y un `run_id` que mintiera sobre cuándo se midió sería peor.
- **`TimeoutStartSec=16h`.** Arriba de la peor corrida observada y debajo de las 24. `run_20260829_020141` tardó
  **12,6 h y terminó bien**, con 3.246 filas: un tope "razonable" de 3 o 6 h habría tirado un día bueno. El
  tope existe solo para que una corrida *colgada* no se coma días enteros en silencio.
- **Un solo reintento** (`Restart=on-failure`, `RestartSec=30min`, `StartLimitBurst=2`), y
  `RestartPreventExitStatus=2 130`: esperar media hora arregla una caída de red, no arregla un `--categoria`
  mal tipeado ni un Ctrl-C.
- **`flock` no bloqueante** en el envoltorio. Si la corrida de ayer sigue viva, la de hoy se retira sin medir en
  vez de correr dos veces en paralelo contra el mismo storefront.

Dos trampas que ya costaron su verificación y que cualquiera que edite esto va a volver a encontrar:
`retail_engine` está instalado **editable solo en `/home/jota/anaconda3/bin/python`** (`/usr/bin/python3` no lo
importa), y el `--canal chrome` por defecto busca `google-chrome` en el `PATH`, que una unidad de systemd no
hereda de tu shell. Por eso el envoltorio fija las dos cosas explícitamente. Si algún día la corrida diaria
falla con `ModuleNotFoundError` o "no se encontró el navegador", empezá por ahí.

`alerta_fallo.sh` **se puede correr a mano sin generar un falso positivo**: consulta el estado real del
servicio y, si no está en `failed`, se anuncia como prueba y no escribe en `fallas.log`. La primera versión no
hacía eso y gritaba "la corrida falló" con `result=success exit=0` en el cuerpo del mismo mensaje. Un avisador
que puede mentir en la dirección de la alarma envenena todas las alarmas siguientes — es el único lugar de este
repo donde un falso positivo cuesta más que un falso negativo.

Las unidades viven **versionadas en `ops/systemd/`** y están enlazadas (`systemctl --user link`) a
`~/.config/systemd/user/`, no copiadas: el repo es la fuente. Editarlas requiere `systemctl --user
daemon-reload`. Depende de `Linger=yes` en el usuario (`loginctl show-user jota`), que es lo que deja correr un
timer de usuario sin sesión iniciada — si alguien lo apaga, la corrida de las 2am deja de existir en silencio.

**El registro a 19 días (medido el 2026-09-24 sobre `runs.jsonl` y los logs del envoltorio).** Del 2026-09-06 al
2026-09-24 hay **19 corridas en 19 días distintos: cero huecos**, las 19 con `corrida_completa=true` y código de
salida 0, `fallas.log` vacío, y el `flock` nunca rechazó una corrida. Eso es lo que la automatización prometía y
es la primera vez que la serie lo tiene. Dos cosas de ese registro valen más que el "19/19":

- **La corrida lenta volvió, y volvió bien.** Mediana de 1,58 h, pero el 2026-09-11 tardó **10,44 h y terminó
  completa**, con 3.118 filas. Es el segundo caso después de las 12,6 h del 2026-08-29, así que una corrida de
  diez horas y pico ya no es una anécdota: un `TimeoutStartSec` de 3 o 6 h habría matado dos días buenos de esta
  serie. No lo bajes contra la mediana.
- **Cero fallas no verifica el avisador.** Nada disparó `OnFailure` en 19 días, así que `alerta_fallo.sh` lleva
  todo ese tiempo probado solo a mano. Ésa es exactamente la razón por la que se lo dejó seguro de correr suelto
  — leé el párrafo de arriba antes de probarlo, porque la prueba se ve idéntica a la alarma real en la pantalla.

**La suite de regresión es el primer paso para verificar cualquier cambio.** 64 funciones `test_` repartidas en
seis archivos bajo `tests/makro_plazavea/`, ninguna de las cuales toca la red:

```bash
python -m pytest tests/ -q                              # los seis de una
python3 tests/makro_plazavea/test_precio_mayorista.py   # cada archivo también corre solo, sale 0/1
```

No hay herramientas de lint ni de build conectadas, y **pytest no es una dependencia declarada** — instalalo, o
corré cada archivo por su cuenta. Las sondas exploratorias no son regresión y las dos cosas no son
intercambiables: una sonda se puede editar o tirar, un archivo de regresión es una promesa. Las cinco sondas
que encontraron el bi-precio ahora viven archivadas en `tests/historia/sondas_makro_plazavea/` y **no corren**;
si el rumbo nuevo necesita scripts exploratorios, van a una carpeta nueva en vez de volver a mezclarse acá.

**Cada archivo de regresión resuelve la raíz del repo como `parents[2]` desde su propia ruta, así que la
profundidad `tests/<colector>/<archivo>.py` es estructural, no cosmética.** Movido un nivel más abajo, la suite
entera muere con `ModuleNotFoundError: No module named 'retail_engine'` — en silencio, si lo que los ejecuta se
traga el código de salida. Ya pasó dos veces: una antes de 1.1.0 (`CHANGELOG.md`, `[1.1.0]`) y otra el
2026-08-30. No reubiques estos archivos sin editar el `parents[2]` de cada uno.

Los seis archivos:
`test_precio_mayorista.py` (23 fichas del storefront, leídas de
`tests/fixtures/makro_plazavea/fichas_publicadas_20260822.csv`), `test_precio_en_quiebre.py` (las seis ramas de
`price_origin`, filas armadas a mano), `test_teaser_de_tarjeta.py` (el régimen y el descuento del teaser de
tarjeta, payloads textuales del crudo), `test_truncamiento.py` (el techo de paginación) y
`test_auditoria_mayorista.py` (la expectativa, la propagación y los estratos de la auditoría mayorista) y
`test_estado_por_corrida.py` (los globales por corrida, cuyo reset vivía inalcanzable dentro de `main()` hasta
v23). Cada uno además imprime un reporte y sale 0/1 cuando se corre solo.

**64 es el conteo de funciones `test_`, que es lo que reporta pytest.** Correr los seis archivos por separado
imprime 86, porque `test_precio_mayorista.py` es *una sola* función que afirma sobre sus 23 fichas. Los dos
números están bien y miden cosas distintas — no "corrijas" uno contra el otro. Los dos se midieron el
2026-08-30, después de archivar `test_propiedades_corrida.py`; el par anterior (73 / 95 sobre siete archivos)
lo contaba, y 9 de esos 73 no afirmaban nada porque su corrida había sido borrada.

Prueban las funciones **puras** y el cableado alrededor de ellas. Eso es un primer paso real y no es todo:
nada de ahí toca la red, así que una suite verde dice que las reglas están intactas, no que la corrida
funciona. **Complementala —nunca la reemplaces— con una corrida viva chica:** `--catalogo 60 --por-categoria 2
--muestra 10 --auditoria 0`, y después leé la salida de consola, `filas.csv` y `run.json` dentro de la carpeta
propia de la corrida bajo `data/makro_plazavea/`. Revisá el código de salida del proceso — es significativo
(ver "el agotamiento del presupuesto es una falla global"). v23 es la razón por la que el orden importa en las
dos direcciones: los 53 tests de v22 estaban verdes y su primera corrida viva murió en el loop de auditoría con
un `KeyError`, porque la fase estaba cubierta y su inicialización no.

Para un cambio que toca reglas de extracción, la prueba más filosa es el criterio de aceptación de
`docs/historia/decisiones_1.1.0.md` §10: volver a medir los 20 SKUs de `golden_v5.csv` con `--skus` y difear
contra el fixture, aplicando los tres grupos de exclusión de esa sección. Cerró en 40/40 el 2026-08-21. Una
función pura se puede probar sin red en absoluto — por eso las reglas viven en funciones puras.

**Esperá 38/40 hoy, no 40/40, y eso es correcto.** El golden lo produjo la sonda v5 bajo la fórmula previa a
v18, así que sus columnas mayoristas están desactualizadas exactamente donde `price != list_price` — medido, 2
de las 40 filas. §10 ahora nombra esas dos filas y las agrega como cuarto grupo de exclusión; leelo ahí en vez
de volver a deducir cuáles son. Una diferencia fuera de ellas es una regresión real. El golden **no** se
regenera: una línea base que se reescribe cada vez que cambian las reglas deja de ser una línea base.

## Convención de versionado — leer antes de editar

**Nunca edites el motor en el lugar bajo la misma identidad, y nunca dejes que dos versiones produzcan salida
bajo el mismo nombre.** Esto no es estilo — es un incidente documentado: se corrió durante horas una copia
vieja del script (`... (1).py`, de una descarga del navegador) y el dataset salió etiquetado en silencio con la
taxonomía equivocada.

Existen tres defensas y tienen que quedar intactas al subir de versión:
1. La versión vive en el **nombre del archivo**, así dos versiones no pueden chocar en disco. *(Ya no es
   literalmente cierto desde la mudanza a `src/`: el colector tiene una única ruta estable,
   `makro_plazavea.py`, así que las defensas 2 y 3 cargan con todo el peso y no se pueden debilitar.)*
2. `VERSION` y el nombre del propio archivo se imprimen en el encabezado de la corrida (`archivo_script`).
3. Los dos se escriben en el manifiesto de la corrida (`run.json`, dentro de la carpeta propia de la corrida),
   así cada dataset se puede rastrear hasta el script exacto.

Al subir de versión: actualizá `VERSION` (`YYYY.MM.DD-NN`) y antepone una entrada de changelog a `CAMBIOS`
describiendo qué cambió. Subí `SCHEMA_VERSION` solo si agregás, sacás o renombrás un campo de `Fila` — se
escribe en cada fila para que un CSV de hace seis meses pueda decir bajo qué reglas nació. La cronología,
porque el salto interesante es el que no agregó nada: v13 y v14 lo dejaron deliberadamente en `"3"` porque
ningún campo cambió; v15 lo subió a `"4"` cuando llegaron las 22 columnas mayoristas (56 → 78); **v18 lo subió
a `"5"` sin agregar una sola columna**, porque `precio_mayorista` cambió de significado (`price − descuento` →
`list_price − descuento`) y una serie que tuviera filas de las dos épocas promediaría dos definiciones
distintas; v20 lo subió a `"6"` con `price_origin` (78 → 79); **v24 lo subió a `"7"` con la cabecera otra vez
idéntica byte a byte**, porque `descuento_monto` / `precio_mayorista` llevaban un descuento de teaser de
tarjeta hasta v23 y llevan el del bi-precio desde v24 — misma columna, dos poblaciones. Hoy: `"7"`, 79
columnas. Así que un cambio de *significado* cuenta como cambio de esquema aunque la cabecera sea idéntica byte
a byte — ése es el caso que la gente olvida, y v18 es el precedente. Como ahora cada corrida escribe su propio
archivo, un cambio de esquema ya no arriesga corromper uno existente — pero `schema_version` sigue siendo lo
que le dice a una capa de consolidación qué corridas puede unir con `UNION` sin riesgo.
Si alguna vez se guarda un archivo de motor viejo al lado del colector actual, no lo borres — documenta la
procedencia de filas de CSV pasadas. (Hoy el árbol solo tiene `makro_plazavea.py`.)

## Principio rector: identificar la sucursal, no suponerla

`Producto + precio + disponibilidad + firma logística + nodo` — nunca `Producto + precio -> adivinar la sucursal`.

La sucursal nunca se infiere del código postal que se *envió*. Se *identifica* leyendo la firma logística que
devuelve VTEX (`warehouseId` + `dockId` + `courierId` + `courierName`) y contrastándola contra las firmas
conocidas de `NODOS` (`identificar_nodo`). Si VTEX resuelve un nodo distinto, la fila lo dice vía
`node_resolved` / `logistics_status` (por ejemplo `MISMATCH_RESOLVED_360`) en vez de etiquetar mal los datos en
silencio. `polygonName` queda excluido del contraste (`Nodo.firma_core()`) porque VTEX lo versiona (`_V2`,
`-V2`) sin que eso signifique que la sucursal cambió — la deriva se registra aparte como `polygon_drift`.

`identificar_nodo` recorre todas las entradas de `NODOS` y devuelve la firma que coincida — una *búsqueda*, no
un lookup indexado por lo que enviaste. Eso es lo que hace que 2 → 20 sucursales salga gratis: el loop, el loop
de medición y los conteos por sucursal del manifiesto iteran todos sobre `NODOS`. Desde 1.1.0 la salida es un
único CSV largo con clave `(run_id, node_id, sku_id)`, así que una sucursal nueva agrega *valores a una
columna* en vez de un archivo (`docs/historia/decisiones_1.1.0.md` §8) — más barato todavía. Para agregar una
sucursal: agregá una entrada (dirección, coordenadas, los cuatro identificadores logísticos, `seller_chain`,
`archivo`). Nada más cambia — pero leé "Escalar a 20+ sucursales" antes de confiar en eso.

`Nodo.archivo` es el único resto: nombraba el CSV de esa sucursal en 1.0.0 y el motor ya no lo lee. Se queda
porque es la forma de rastrear un `makro_359_santa_anita.csv` de la historia vieja hasta su nodo.

## Principio rector: encontrar el almacén no es confirmar el despacho

VTEX puede ofrecer varias SLAs para el mismo ítem. Que una venga del almacén esperado **no** significa que sea
la que el cliente recibiría — eso lo dice `selectedSla`. Hasta v08 el script tomaba la primera SLA cuyo almacén
coincidiera y la llamaba `MATCH`, lo que sobreinterpretaba la evidencia.

Por eso `fulfillment_confirmed` (`SI`/`NO`) va separado de encontrar el almacén correcto:
- selección explícita de VTEX, o la única SLA inequívoca cuando hay una sola → confirmado
- varias SLAs ofrecidas y VTEX no eligió → **no** confirmado, aunque el almacén coincida

Cuando el almacén coincide pero el despacho no está confirmado: `logistics_status = MATCH_SIN_CONFIRMAR` y
`price_status = QUALIFIED` — ni `VERIFIED` ni `UNVERIFIED`. No colapses esto en `MATCH` por comodidad; un caso
ambiguo tiene que quedar ambiguo en los datos.

## Principio rector: el precio nunca se descarta

Si VTEX devolvió un precio, se escribe al CSV — aunque la validación logística haya fallado. Tres columnas,
deliberadamente separadas:

- `price` — el **hecho** (qué contestó VTEX)
- `price_origin` — la **procedencia** (`MEDIDO` / `LISTA_SIN_PROMO`)
- `price_status` — el **juicio** (`VERIFIED` / `VERIFIED_SELLER_RAIZ` / `QUALIFIED` / `UNVERIFIED` / `NO_PRICE`)
- `logistics_status` — el **porqué** (`MATCH`, `MATCH_SELLER_RAIZ`, `MATCH_SIN_CONFIRMAR`, `SIN_STOCK`,
  `NO_COVERAGE`, `OPERADOR_EXTERNO`, `MISMATCH_RESOLVED_*`, `HTTP_ERROR`, `EXCEPTION`, ...)

`price_origin` llegó en v20 y es la más nueva de las cuatro. **VTEX no evalúa promociones cuando el nodo no
tiene stock — devuelve el precio de lista**, y el motor solía escribir ese número en `price` sin forma de
distinguirlo de uno cotizado. Medido: las 143 filas `withoutStock` de `run_20260822_020027` tienen todas
`price == list_price` y `discount_pct = 0.00`, contra un 19.8% de incidencia de promo entre las filas con
stock. Las consecuencias van más hondo que una columna, y las tres son estructurales:

- `discount_pct` queda **vacía** bajo `LISTA_SIN_PROMO`. Un `0.00` ahí afirma "este producto no tiene
  descuento" sobre una promoción que nadie evaluó. Con stock, `0.00` es una medición genuina y tiene que
  sobrevivir — la misma celda significa dos cosas distintas según el origen, y por eso el origen es un
  parámetro de `calcular_descuento_pct` y no algo que pueda inferir de los números.
- La regla de publicación del escalón mayorista (`list_price − descuento >= price` → suprimido) se vuelve
  **inevaluable**: con `price == list_price` se cumple por construcción aritmética, así que 82 de 82 filas sin
  stock habrían quedado marcadas como publicadas sin haber medido nada. El precio igual se calcula
  (`list_price` es dato de catálogo, el descuento sale del teaser — ninguno depende del stock); lo que se
  retiene es el veredicto. Eso es `BIPRECIO_PUBLICACION_INDETERMINADA`.
- `clasificar_origen_precio` se apoya en `availability`, **nunca** en la relación `price`/`list_price`.
  `price == list_price` también le pasa a filas con stock sin promoción; el stock es la causa y la igualdad de
  precios es su consecuencia, y una consecuencia no puede ser el criterio.

Una fila con `price_status=UNVERIFIED` es señal real (ese SKU no tiene cobertura desde esa sucursal), no ruido
para filtrar en el momento de la extracción. `medir()` y `construir_fila()` nunca lanzan excepción por un
problema de un SKU — una medición fallida se vuelve una `Fila` con campo `error`, así un SKU malo no puede
matar una corrida. No "limpies esto" con returns tempranos que salteen escribir una fila.

## Principio rector: el agotamiento del presupuesto es una falla global, no un SKU malo (v12/v13)

Ésta es la regla más nueva y la que más fácil se rompe. `TopeAgotadoError` (con `TopeAgotadoAlEntrarError` /
`TopeAgotadoEnReintentoError`) es una condición **a nivel de corrida**, no a nivel de SKU. Nunca la puede
atrapar el `except` por SKU de `medir()` y convertirla en una fila `EXCEPTION`/`NO_PRICE` — eso disfrazaba una
corrida muerta de corrida llena de productos no disponibles. Cuando se dispara, la medición se detiene, lo que
ya se midió se conserva, y las mediciones *pendientes* quedan listadas en `manifiesto.medicion` (sin filas
inventadas).

Las consecuencias están codificadas, no narradas:
- `evaluar_corrida()` decide `corrida_completa`, `motivos_fallo_global` y el **código de salida del proceso**.
- Un límite de descubrimiento *deliberado* (`--catalogo`, `--por-categoria`, `--presupuesto-descubrimiento`) no
  es una falla — sale **0**, con `descubrimiento.clasificacion` diciendo que el catálogo quedó parcial a
  propósito (`LIMITE_POR_CATEGORIA`, `LIMITE_PRESUPUESTO_DESCUBRIMIENTO`).
- Medición o stock de cadena cortados por presupuesto → `medicion.completa=false` /
  `stock_cadena.completo=false` → sale **1**. La corrida entregó menos de lo que prometía la selección, y nadie
  pidió eso.
- Playwright ausente → sale **2**; Ctrl-C → sale **130**.

También de v13: se dispara una alarma temprana por nodo si un nodo acumula 5 o más mediciones sin ningún
`MATCH`/`MATCH_SELLER_RAIZ`/`MATCH_SIN_CONFIRMAR`, así una firma de `NODOS` mal cargada no puede quemar una
corrida entera produciendo solo `OPERADOR_EXTERNO` en silencio.

## Principio rector: sin archivos de entrada — el catálogo se descubre en cada corrida (v11)

El motor **no lee nada** para decidir qué medir. `panel.json` (una canasta congelada en disco, v01–v10) ya no
está. Resolvía un problema real —muestreo aleatorio significa que dos corridas no comparten ningún SKU y no hay
serie temporal— pero era una muleta del *muestreo*, y traía su propio bug: el stock de cadena salía de esa foto
vieja y parecía en vivo (0 de 100 SKUs cambiaron entre corridas). Cualquier campo leído del panel es el pasado
disfrazado de presente.

La reproducibilidad ahora sale de un hash determinista en vez de un archivo (`seleccionar`):

```
orden = md5(f"{semilla}:{sku_id}")   ->  take the first N
```

Misma semilla + mismo catálogo = misma muestra, sin persistir nada; y los productos nuevos de Makro entran al
sorteo solos. **Todo archivo que el motor escribe es salida. Ninguno se vuelve a leer para decidir qué medir.**
No reintroduzcas un archivo de entrada para "estabilizar" la muestra.

## Principio rector: el alcance se elige, no se hereda del árbol (v17)

El árbol de categorías tiene **3.401 nodos**, y `descubrir_catalogo` los recorre en un orden fijo —por ruta—
deteniéndose cuando se alcanza `--catalogo N`. El orden fijo es deliberado y tiene que quedarse: es lo que hace
que el descubrimiento sea reproducible sin persistir nada.

Pero traía una consecuencia que nadie eligió. Una corrida acotada siempre medía las *primeras* categorías del
árbol —Packs Limpieza, Packs Desayunos, Packs Vinos— y abarrotes no podía aparecer nunca. Así que
`--catalogo 300` nunca significó "300 SKUs del catálogo"; significaba "los primeros 300 que aparezcan", una
muestra sesgada por una propiedad accidental de cómo VTEX ordena su propio árbol. El alcance de la serie lo
estaba decidiendo el árbol, no el analista. Eso es lo que arregla `--categoria`.

`--categoria "/399/,/77/"` restringe el universo antes de recorrer nada. Tres cosas de este flag son
estructurales:

- **El filtro corre antes del recorrido, no después.** Esto no es una optimización, es si el flag funciona o
  no: descartar una categoría *después* de paginarla cuesta un request por cada categoría tirada — 3.300
  categorías a `--intervalo 1.5` son unos 83 minutos gastados para no producir nada. Medido sobre `/399/`
  (Limpieza): 47 categorías recorridas, **3.353 salteadas sin un solo request**.
- **Coincidencia de prefijo por segmento, nunca `startswith` sobre el string.** Pedir `/39/` no puede arrastrar
  `399`, que es otra rama completamente distinta. Contrastar segmentos enteros es además lo que hace que pedir
  un padre incluya a sus hijos, que es el comportamiento útil.
- **Una ruta que no está en el árbol es un error de argumento (salida 2), no una advertencia.** Una corrida que
  mide cero categorías porque alguien tipeó `/3999/` terminaría si no con salida 0 y un CSV vacío —
  indistinguible de una sucursal que genuinamente se quedó sin stock. Todas las rutas malas se reportan juntas,
  así quien pasó cuatro y tipeó mal dos no se entera de a una. La verificación necesita el árbol vivo, así que
  ocurre dentro del descubrimiento y no en `parsear_argumentos`.

`--categoria` es incompatible con `--skus`: ese flag ya nombra exactamente qué medir, así que aceptar un filtro
que no cambia nada implicaría que se aplicó alguno. Se compone libremente con `--catalogo` y `--por-categoria`,
que siguen operando *dentro* del universo restringido.

### Qué significa `completo` cuando el alcance está restringido

Ésta es la parte que puede corromper una conclusión en silencio. Una corrida filtrada que recorre todo lo que
se le pidió recorrer está completa — pero completa *respecto del pedido*, no respecto del catálogo de Makro.
Leer una cosa como la otra permitiría que una serie construida sobre una sola rama se reporte después como
cobertura de categoría.

Por eso el manifiesto separa las dos:

- `descubrimiento.alcance` — `CATALOGO_COMPLETO` o `CATEGORIAS_SELECCIONADAS`.
- `descubrimiento.clasificacion` — `COMPLETO` solo para un recorrido completo sin filtrar;
  **`COMPLETO_EN_CATEGORIAS`** cuando se agotaron las ramas pedidas. La misma garantía de que no falta nada,
  otro universo, y el nombre dice cuál.
- `categoria_filtro`, `categorias_seleccionadas`, `categorias_a_recorrer` — sin esto no se puede reconstruir el
  alcance de una corrida vieja: dos corridas con la misma cantidad de SKUs pueden haber mirado ramas distintas.

`--por-categoria` mantiene su contrato más viejo y más fuerte por encima de todo esto: con ese flag puesto, el
descubrimiento **nunca** se puede marcar completo, filtrado o no. La promesa de ese flag es "muestra ancha", y
ninguna restricción de alcance convierte una muestra en un censo.

## Principio rector: dos clases de stock responden dos preguntas distintas

`availability` sale del checkout **con la dirección de la sucursal** ("¿esta tienda lo puede despachar hoy?").
`chain_stock` sale del catálogo **sin contexto de sucursal** ("¿cuánto queda en cualquier parte de la
cadena?"). Se refrescan de forma independiente (`refrescar_stock_cadena`, un puñado de requests `fq=productId:`
en lote, no uno por SKU) y no se supone que coincidan.

`calcular_stock_signal` los cruza en una sola etiqueta accionable:
- `DISPONIBLE` — la sucursal lo tiene
- `SIN_STOCK_LOCAL_CADENA_CON_STOCK` — la sucursal no lo tiene, la cadena sí — **la fila que vale plata**:
  demanda real, quiebre visible, comparable contra la competencia
- `SIN_STOCK_CADENA` — no queda en ninguna parte
- `SIN_STOCK_LOCAL_CADENA_DESCONOCIDA` — la sucursal no lo tiene, el estado de la cadena se desconoce

Éstos eran `QUIEBRE_LOCAL` / `QUIEBRE_CADENA` / `QUIEBRE_LOCAL_CADENA_DESCONOCIDA` hasta v20. "Quiebre" afirma
un faltante *temporal* de algo que la sucursal normalmente tiene, y eso no es lo que la medición de un solo día
puede distinguir de que el SKU simplemente no esté en el surtido de esa sucursal — de las 143 se revisó una a
mano (SKU 11566889), no 143. Los nombres nuevos declaran lo que se observó y dejan la causa sin afirmar; la
serie lo resuelve sola, y esa inferencia le pertenece al analista que tiene 30 días de eje temporal enfrente,
no a una celda.

## `surtido_makro` no responde la pregunta de surtido (medido 2026-08-25)

La columna afirma ser "la ÚNICA prueba de surtido Makro para ESTA sucursal"
(`docs/historia/decisiones_1.1.0.md` §2). No lo es. Medido sobre las dos corridas que hay en disco, es
**colineal con `availability` en cada una de las filas**:

| corrida | `SI` / `available` | `NO` / `withoutStock` | excepciones |
|---|---|---|---|
| `run_20260822_020027` | 3031 | 143 | **0** |
| `run_20260824_154502` | 3029 | 143 | **0** |

Es igual de colineal con `logistics_status` (`MATCH` / `SIN_STOCK`) y con `fulfillment_type` (`tienda` /
vacío). Cuatro columnas, un solo hecho.

**El mecanismo — y no es el que uno adivinaría.** `seller_chain` no vuelve *vacía* cuando no hay stock; se
**colapsa al seller raíz**:

```
surtido_makro = SI  ->  seller_chain = "1 > plazaveamko359" (1520) / "1 > plazaveamko360" (1511)
surtido_makro = NO  ->  seller_chain = "1"                  (143)
```

La regla es `"SI" if f"plazaveamko{node_id}" in seller_chain else "NO"`. VTEX solo agrega el seller de la
sucursal una vez que resolvió un seller que efectivamente va a despachar, y eso requiere stock. Así que la
columna no pregunta "¿este SKU es parte del surtido de esta sucursal?" — pregunta **"¿VTEX resolvió hoy esta
sucursal como seller?"**, que es verdadero si y solo si hay stock.

**Es colineal, no un alias, y la diferencia es estructural.** Una fila con stock despachada por el seller raíz
o por un tercero (`MATCH_SELLER_RAIZ`, `OPERADOR_EXTERNO`, dropship) volvería con `availability = available` y
`surtido_makro = NO`. En abarrotes, sobre los nodos 359 y 360, eso no pasó nunca: las dos corridas contienen
solo `MATCH` y `SIN_STOCK` — cero `MATCH_SELLER_RAIZ`, cero `OPERADOR_EXTERNO`. La redundancia es una propiedad
de *esta* muestra, no de la definición. Quien "simplifique" la columna apuntándola a otro campo estaría
arreglando la cosa equivocada.

**Hoy no se borra (§6.5, §6.6).** §6.6 dice que una columna que parece inútil se mide durante 30 días antes de
sacarla, y una categoría sobre dos nodos es exactamente la evidencia delgada que esa regla existe para anular.
§6.5 hace el mismo argumento sobre este mismo caso, tres años antes: `seller_id` se queda porque *"si aparece
un tercero de marketplace va a llevar otro valor"* — que es precisamente la fila que rompería la colinealidad.
**Revisar a los 30 días, o cuando el alcance se expanda de abarrotes a otra categoría.** Hasta entonces la
columna se queda, redundante y documentada.

**La pregunta que se suponía que respondía sigue abierta.** "¿Este SKU pertenece al surtido de este nodo?" hoy
no la responde **ninguna columna del esquema**, y no la va a responder una celda. Responderla bien significa
consultar el catálogo bajo contexto de sucursal — una fase de requests nueva por sucursal, que es el costo que
peor escala de todo lo que hay acá cuando 2 sucursales se vuelven 20. El eje temporal la responde gratis: un
SKU que a lo largo de 30 días nunca aparece con stock en un nodo, con alta probabilidad no está en el surtido
de ese nodo. El mismo razonamiento que llevó a v20 a renombrar `QUIEBRE_LOCAL` — la causa la resuelve la serie,
no la afirma una celda, y esa inferencia le pertenece al analista.

## Principio rector: la evidencia es un seguro barato, verificar en vez de confiar

Tres bugs de parseo distintos (`all_headers` en vez de `headers`, un fallback destructivo, y un `sellerChain`
que descartaba nodos válidos por error) costaron cada uno volver a scrapear historia. Hoy existen dos defensas:

- **Evidencia cruda** (`guardar_evidencia`) — cada respuesta se archiva, comprimida, en `raw.jsonl.gz` dentro
  de la carpeta propia de la corrida, al lado de las filas que explica (headers y cookies excluidos a propósito
  — los tokens de sesión nunca tocan el disco). Un arreglo de parseo se puede reproducir contra el archivo con
  cero requests nuevos.
- **Reconciliación muestreada** (`--auditoria`, 5% por defecto) — esa proporción de mediciones se pide de las
  *dos* formas y se difea (`reconciliar`) sobre precio, disponibilidad, almacén y cadena de sellers. Se confía
  en `simulation` por defecto porque es 1 request en vez de 3; esto es lo que sigue verificando esa confianza
  sin pagar 3x.

Desactivalas (`--sin-evidencia`, `--auditoria 0`) solo para corridas de prueba descartables.

## Principio rector: un cambio de esquema no debe corromper la historia

`COLUMNAS = list(Fila().__dict__.keys())`, así que agregar un campo agrega automáticamente una columna al CSV.
Hasta 1.0.0 eso era peligroso, porque al CSV se le hacía *append*: una cabecera vieja recibía en silencio filas
más anchas y desalineaba toda lectura aguas abajo. Una guarda (`archivar_si_cambio_el_esquema`) renombraba el
archivo viejo antes de que eso pudiera pasar.

1.1.0 sacó la guarda, y vale saber por qué para que nadie la extrañe. Con una carpeta inmutable por corrida,
cada corrida escribe su propio `filas.csv` con su propia cabecera y nunca lo vuelve a abrir — dos esquemas ya
no pueden compartir un archivo, así que no queda nada que desalinear. Lo que protegía la guarda ahora lo carga
`schema_version`, escrito en cada fila.

El riesgo no desapareció, se mudó: un glob sobre `run_*/filas.csv` todavía puede juntar corridas con cabeceras
distintas. Ése es el trabajo de la capa de consolidación, y `schema_version` es con lo que decide. No edites
una cabecera a mano para hacer desaparecer un desajuste.

## Principio rector: el precio mayorista se reconstruye, no se observa

La razón de existir de 1.1.0 es el bi-precio: Makro descuenta un SKU al pasar un umbral declarado
(`CantidadBiPrecioMK`), y ése es el número contra el que un analista de pricing realmente negocia. El
mecanismo y las 22 columnas están especificados en `docs/historia/decisiones_1.1.0.md` §1 y §5 — leelos ahí, no
los repitas acá. **Una excepción, y es la fórmula misma:** §1 y §5 todavía llevan la versión previa a v18,
`precio_mayorista = price − descuento`, que v18 midió equivocada (11/23 contra las fichas del storefront). La
fórmula actual es `list_price − descuento` y vive en `calcular_mayorista`, con la corrección registrada en
`docs/historia/brief_correccion_mayorista.md` y en la sección `[Sin publicar]` de `CHANGELOG.md`. §1 tiene
razón en todo lo demás — la fuente del umbral, el escalón único, el teaser, el detector.

Lo que corresponde acá es la epistemología, porque es fácil equivocarse dos veces:

- El **umbral** existe solo en la respuesta del catálogo; el **descuento** aparece tanto en el catálogo como en
  `simulation`. Ninguna fuente alcanza sola, así que `Producto` transporta los campos que solo están en el
  catálogo desde el descubrimiento hasta la medición. Eso *no* es un regreso del archivo de entrada
  `panel.json` — no se lee nada del disco; es la respuesta de catálogo de una corrida viajando en memoria hasta
  la medición de esa misma corrida.
- El motor mide a `qty=1`, y a `qty=1` el descuento no se aplica. Así que `precio_mayorista` está
  **reconstruido por resta**, no observado. `--auditoria-mayorista` es lo único que lo observa: vuelve a medir
  unas pocas filas a `qty = bi_umbral` y compara. Las filas que tocó dicen `precio_mayorista_verificado = SI`;
  todas las demás dicen `NO`, y `NO` es la respuesta honesta por defecto, no un hueco.
- Cuando el precio medido no coincide con el reconstruido, gana el valor **medido** (es lo que paga el
  cliente), la fila se marca con `DQ_MAYORISTA_DISCREPA`, y `descuento_monto` se deja intacto — el desacuerdo
  entre lo que VTEX *declaró* y lo que *cobra* es el hallazgo, así que borrar cualquiera de los dos lados lo
  borraría.
- **Se disparó exactamente una vez, y el lado equivocado fue la auditoría.** SKU 10012680 en
  `run_20260822_020027`, umbral 3: VTEX cobró 88.00 — exactamente su propio `price`, contra un `list_price` de
  118.50. No había desacuerdo. La promoción unitaria simplemente le ganó al escalón declarado, y la auditoría
  estaba comparando contra una expectativa que no aplicaba. Leé un `DQ_MAYORISTA_DISCREPA` como *"uno de estos
  dos lados está mal"*, nunca como *"VTEX está mal"* — la bandera nombra una contradicción, no un culpable.
- **Hay dos expectativas a `qty = bi_umbral`, no una, y eso es todo el contenido de
  `BIPRECIO_SUPERADO_POR_PROMO`.** Cuando `list_price − descuento >= price`, el escalón declarado es peor que
  la promoción que ya corre a `qty=1`, así que no se publica ni se cobra, y el precio a esperar es `price`
  mismo — no la reconstrucción. Ese estado, entonces, *predice un número*, que es lo que lo hace auditable, y
  hasta v22 era la única afirmación del bi-precio que nada verificaba.
  `BIPRECIO_PUBLICACION_INDETERMINADA` queda excluido de la auditoría por la razón espejo: sin stock VTEX no
  cotiza ninguna cantidad, así que no hay nada que comparar y contarlo fabricaría coincidencia a partir del
  silencio. Definir los estados en sí le corresponde a `docs/historia/decisiones_1.1.0.md` §5 (su enum está
  anotado ahí como superado); lo que corresponde acá es la regla que los produjo — un estado que predice un
  precio tiene que ser auditado, y uno que no predice nada nunca se puede contar como acierto.
- **El rango de umbrales verificado bajo la fórmula *actual* es más angosto de lo que se lee en §11.** La
  evidencia 12 / 15 / 24 de §11 se midió el 2026-08-21 y el 2026-08-22 bajo la base previa a v18, y una
  auditoría aprobada de una fórmula superada no se transfiere a su reemplazo. Desde v18 las auditorías que hay
  en disco cubren los umbrales **2, 3, 4 y 20**, contra un catálogo que declara 2, 3, 4, 6, 10, 12, 13, 15 y
  20 — así que 6, 10, 12, 13 y 15 están hoy sin verificar. Citá §11 como el registro de lo que se verificó
  *entonces*.
- La auditoría muestrea por **cobertura de umbrales**, no por representatividad —uno bajo, el más alto
  disponible, después los umbrales no vistos— y desde v22 además estratifica por promoción unitaria, que es el
  eje donde la fórmula realmente se rompió. Tres auditorías del mismo umbral prueban la fórmula solo para ese
  umbral.

## Arquitectura (`src/retail_engine/collectors/makro_plazavea.py`, un solo archivo, de arriba abajo)

1. **Bloque de versión + `CAMBIOS`** — `VERSION`, `SCHEMA_VERSION`, y el changelog por versión descrito arriba.
2. **Constantes + rutas de salida** — `MOTOR = "makro_plazavea"` nombra el namespace de salida para que
   colectores futuros no puedan chocar. El colector se nombra por **fuente**, no por minorista: `makro_pe`
   sería el mismo minorista a través de otro storefront y no debe compartir carpeta. `RETAILER` viaja *dentro*
   de cada fila. `raiz_repo()` encuentra el repo por marcador (`pyproject.toml`) en vez de contar padres —
   antes de 1.1.0 la raíz de salida se resolvía *adentro* del paquete instalable. `carpeta_corrida()` es la
   única función que arma rutas de salida; si encontrás un `SALIDA / "algo"` en cualquier otro lado, es un bug.
3. **`Nodo` / `NODOS`** — catálogo de firmas de sucursal; `firma_core()` y `direccion()`.
4. **`Producto` / `Fila`** — `Producto` es una entrada de catálogo; `Fila` es una medición (SKU × sucursal ×
   momento) y sus campos son el esquema del CSV. Cada fila lleva `run_id`, `schema_version`, `retailer` y
   `dq_flags`.
5. **`Cliente`** — envoltorio educado sobre el `APIRequestContext` asíncrono de Playwright (*no* `page`):
   intervalo mínimo entre requests, backoff exponencial ante 429/5xx, respeta `Retry-After`, tope duro de
   requests aplicado **por intento** (v12), contadores de requests desglosados por fase. Ojo con el comentario
   sobre `APIResponse.headers`, que es una *property*, no una corrutina. Los bugs de `ERRORES_DE_CODIGO`
   (`AttributeError`, `TypeError`, ...) deliberadamente **no** se reintentan.
6. **Descubrimiento** (`total_desde_resources`, `aplanar_categorias`, `parsear_producto`,
   `descubrir_catalogo`, `es_basura`, `es_landing_seo`) — recorre el árbol de categorías de VTEX armando la
   ruta completa de cada categoría, pagina por longitud de página cuando el header `resources` no se puede
   parsear (v12), y limita las landings SEO (nombres en minúscula como "absolut vodka") a 1 SKU para que no
   inunden la muestra con las variantes de un solo producto. La ruta completa que `aplanar_categorias` ya arma
   es lo que hace que `--categoria` sea una coincidencia de prefijo y no un nuevo recorrido del árbol:
   `filtrar_categorias`, `ruta_bajo` y `normalizar_ruta_categoria` son puras y testeables sin red, y el filtro
   se aplica a esa lista plana *antes* del loop (ver "el alcance se elige, no se hereda del árbol").
7. **Selección** — `seleccionar` es el muestreo determinista por hash descrito arriba; no persiste nada.
   `descubrir_por_skus` es el camino de `--skus`: resuelve una lista explícita con `fq=skuId:` en lote (20 SKUs
   en 2 requests) y devuelve tres listas, porque "se preguntó y el catálogo dijo que no"
   (`SKU_NO_ENCONTRADO`, que sí obtiene una fila) no es "nunca se preguntó, se acabó el presupuesto" (que no
   obtiene fila).
8. **Evidencia y auditoría** (`guardar_evidencia`, `reconciliar`) y **stock de cadena**
   (`refrescar_stock_cadena`, `calcular_stock_signal`).
9. **Parseo de respuestas** (`extraer_logistica`, `evaluar_calidad`, `extraer_item`, `extraer_direccion`,
   `clasificar_fulfillment`, `identificar_nodo`) — `clasificar_fulfillment` distingue *quién despacha
   realmente* (`tienda`, `tienda_raiz`, `proveedor`/dropship, `generico_pv`, `operador_externo`,
   `desconocido`), porque el storefront también vende inventario que no es de Makro. `operador_externo` llegó
   en 1.1.0 por un caso real (`STK917NF` / `DCK-NF-MK-917` / `DD-NF-CD-917-URBANO`): el origen estaba
   completamente identificado y simplemente no era Makro. Llamar a eso `desconocido` invitaba a tratar un hecho
   firme sobre el surtido como dato faltante, así que `desconocido` ahora queda reservado para logística
   *realmente ausente*. `evaluar_calidad` corre cuatro reglas puntuales (`DQ_SKU_DISTINTO`, `DQ_PRECIO_MAYOR`,
   `DQ_PRECIO_CERO`, `DQ_UNIDAD_INCONSISTENTE`) que cada una atrapó una fila mala real — se mantiene chico a
   propósito. Una quinta, `DQ_MAYORISTA_DISCREPA`, la pone la auditoría mayorista y no `evaluar_calidad`.

   También acá, todas puras: `calcular_mayorista`, `clasificar_ean`, `resolver_presentacion` y los lectores del
   teaser (`leer_regimen`, `leer_descuento`). Toman JSON o strings y devuelven valores — sin red, sin `Fila` —
   así que se pueden testear directo, y `enriquecer_fila` es deliberadamente un pegamento delgado sobre ellas.
10. **Medición** (`consultar_simulation`, `consultar_orderform`, `_orderform_con_cliente`, `construir_fila`,
    `medir`) — `simulation` es 1 request sin estado; `orderform` es el flujo de 3 requests y el único que
    devuelve una dirección resuelta por el backend, así que desde v10 tiene **su propio contexto de navegador**
    (el carrito es estado y estaba contaminando las mediciones). El modo por defecto cae a orderform por SKU
    solo cuando simulation no devolvió logística *y* la disponibilidad no era ya un `cannotBeDelivered` /
    `withoutStock` definitivo, y nunca pisa un precio de simulation con un resultado vacío de orderform.
11. **Salida** (`escribir_filas`, `registrar_corrida`, `evaluar_corrida`, `escribir_manifiesto`) — una carpeta
    inmutable por corrida, un CSV largo adentro. La propiedad de serie temporal ya no es "hacerle append a un
    archivo" sino "acumular carpetas": leer toda la historia es un glob sobre `run_*/filas.csv`. **Nunca pises
    la carpeta de una corrida pasada.** El manifiesto registra la versión de script y de esquema, el ID de la
    corrida, los requests por fase, los conteos por sucursal, los bloques de completitud `descubrimiento` /
    `medicion` / `stock_cadena`, `seleccion` (incluyendo `modo_seleccion` y, para `--skus`, qué SKUs estaban
    ausentes frente a cuáles nunca se preguntaron), `auditoria_mayorista`, y `resumen.corrida_completa` +
    `motivos_fallo_global`.
12. **`main()`** — parseo de CLI, ciclo de vida del navegador, reset del estado global por corrida (`MEDICION`,
    `STOCK_CADENA_ESTADO`, `ALARMA_FIRMA_DISPARADA` — son globales de módulo para que `escribir_manifiesto`
    pueda leerlos; mantenelos limpiados por corrida así una segunda llamada en el mismo proceso arranca
    limpia), una advertencia si todavía quedan CSVs previos a v11 en la vieja raíz de salida plana (nunca los
    mueve), dimensionamiento automático del tope de requests, y el veredicto final de `evaluar_corrida` + el
    código de salida.

## Escalar a 20+ sucursales

Más sucursales, la misma arquitectura — la mayor parte de la maquinaria ya escala porque itera sobre `NODOS`:

- **Gratis**: el loop de búsqueda de `identificar_nodo`, el loop de medición, un valor más de `node_id` dentro
  del único CSV largo (`docs/historia/decisiones_1.1.0.md` §8), el desglose por sucursal del manifiesto, y el
  dimensionamiento automático del tope de requests.
- **Crece linealmente, hay que planificarlo**: requests por corrida (SKUs × sucursales, ~×4 en el peor caso) y
  tiempo de reloj (los requests son estrictamente secuenciales a `--intervalo`, por diseño — "respeto al
  servidor" en el docstring del módulo). 2 → 20 sucursales es aproximadamente una corrida 10x. Dimensioná
  `--muestra` e `--intervalo` en consecuencia en vez de subir `--tope` a ciegas — y notá que `--categoria` es
  la otra palanca: a 20 sucursales, angostar el universo a las categorías que importan comercialmente le gana a
  medir una tajada ancha y superficial de todo.
- **El riesgo real al incorporar una sucursal**: una entrada de `NODOS` equivocada o incompleta no da error —
  VTEX simplemente nunca devuelve esa firma, cada fila queda como `node_resolved="OTHER"` /
  `OPERADOR_EXTERNO`, y la sucursal parece "cubierta" en el conteo de filas mientras aporta cero precios
  verificados. La alarma por nodo de v13 ahora avisa durante la corrida, pero igual: **antes de confiar en una
  sucursal nueva, corré una `--muestra` chica contra ese nodo solo y confirmá que las filas vuelven `MATCH` /
  `MATCH_SELLER_RAIZ`.**
- **Fuera de alcance acá**: adaptadores multi-minorista y un modelo de dominio agnóstico del minorista
  (Nivel 3+ de la escalera correctitud-antes-que-cobertura). No empieces a construir esas abstracciones de
  forma especulativa — la división de un namespace por colector es la única concesión que el motor le hace a
  ese futuro.

## Archivos de salida

Una carpeta inmutable por corrida, nombrada exactamente como el `run_id`
(`docs/historia/decisiones_1.1.0.md` §7):

```
data/makro_plazavea/
├── run_20260821_201821/
│   ├── filas.csv          una fila por (SKU, sucursal, corrida) — formato largo, node_id es columna
│   ├── run.json           el manifiesto de esta corrida
│   └── raw.jsonl.gz       respuestas crudas de VTEX, comprimidas
├── run_20260822_001329/   otra corrida, el mismo día o no
├── runs.jsonl             índice append-only, una línea plana por corrida
└── last_run.json          copia del manifiesto más nuevo, para no tener que hacer un glob
```

- **Nombres fijos adentro, el `run_id` solo en la carpeta.** Un identificador, imposible de desincronizar.
- **`YYYYMMDD_HHMMSS`, así el orden alfabético es cronológico.** Leer toda la historia es
  `read_csv('data/makro_plazavea/run_*/filas.csv')`.
- **Nunca un CSV por sucursal.** El nodo es la columna `node_id`, así que una sucursal 21 agrega valores, no
  archivos, y nunca cambia el esquema (§8). Medido sobre `golden_v5.csv`: 24 de las 56 columnas del motor
  difieren entre 359 y 360 para al menos un SKU, y solo 11 de ésas son identidad del nodo — el formato ancho se
  vuelve más caro con cada sucursal, no menos.
- **`runs.jsonl` es lo único append-only que queda**, a propósito: es un índice, no un dataset, y sigue siendo
  legible con `tail` a las mil corridas.
- El motor **nunca escribe exports**. Los cortes por sucursal o por categoría salen de un comando aparte.

### Columnas que pueden salir estructuralmente vacías

Cinco columnas están vacías en **todas** las filas de todas las corridas que hay en disco — medido el
2026-08-26 sobre 9.528 filas de las cuatro corridas que tienen un `filas.csv`. Las cinco están declaradas
`str = ""` en `Fila`, así que una capa de consolidación que infiera tipos a partir de una muestra las va a
tipar como float/`NaN` y después las va a comparar contra `""` sin encontrar nada. La misma regla que para los
campos monetarios, aplicada al tipo: **vacío significa desconocido**, y un cero —o un `NaN`— nunca reemplaza a
un desconocido.

| columna | se llena cuando |
|---|---|
| `postal_resolved` | la fila vino de una respuesta de **orderForm** (`--modo orderform`, o el fallback por SKU). `simulation` no devuelve ningún `shippingData`, así que el modo por defecto nunca la llena. |
| `neighborhood_resolved` | misma fuente, misma condición. |
| `sla_selected` | VTEX marca un `selectedSla` — cosa que `simulation` nunca hace *por diseño*, no por ambigüedad. Ésa es exactamente la razón por la que una SLA única cuenta como confirmada; ver "encontrar el almacén no es confirmar el despacho". |
| `polygon_drift` | el `polygonName` devuelto difiere del que está registrado en `NODOS` para ese nodo. |
| `error` | HTTP ≥ 400, un `__error` dentro de un cuerpo 200, o una excepción por SKU. Las cuatro corridas no tuvieron ninguna de las tres, que es el buen resultado, no un hueco. |

**Ninguna de las cinco es código muerto.** El par de dirección y `error` son alcanzables desde el camino de
orderForm y desde los caminos de falla; las respuestas de orderForm de `--auditoria` sí llevan un
`shippingData.address` poblado, que se lee para la reconciliación y después se descarta, así que el valor
existe y simplemente no es el de la fila. `docs/historia/decisiones_1.1.0.md` §11 ya registra la tasa de
llenado de `postal_resolved` / `neighborhood_resolved` como una pregunta abierta a 30 días.

**`dq_flags` no es una de ellas, y es la trampa.** Lleva `DQ_MAYORISTA_DISCREPA` en exactamente una fila de
`run_20260822_020027` — no vacía 1 vez sobre 9.528. Una sola corrida la va a mostrar 100% vacía y un muestreo
casi seguro va a coincidir; los dos están mal. Tipala desde `Fila`, nunca desde una muestra.

El analista hace la comparación entre sucursales aguas abajo (SQL/pandas sobre el CSV largo) — no le agregues
lógica de comparación entre sucursales a este motor, ni a 2 sucursales ni a 20.

## Notas para sesiones futuras

- **`docs/` tiene tres pisos y el nombre de la carpeta dice cuál.** `docs/columnas.md` está en la raíz y es
  vivo: es autoritativo sobre qué significa cada celda de `filas.csv`. `docs/historia/` es el registro cerrado
  de la era 1.1.0 — todavía se cita, nunca es hoja de ruta; no cites números de sección de ahí como si fueran
  actuales, y no actúes sobre un brief sin revisar git primero. `docs/bodegueros/` es el rumbo nuevo y todavía
  no es dueño de nada sobre el motor.
- **Este archivo, `docs/columnas.md` y `docs/historia/decisiones_1.1.0.md` son los documentos de contexto
  autoritativos del repo**, cada uno sobre una cosa distinta: las razones de diseño acá, el significado de las
  celdas en `columnas.md`, el inventario de 1.1.0 en `decisiones_1.1.0.md`.
- `docs/historia/brief_correccion_mayorista.md`, `docs/historia/brief_tareas_bloqueantes.md` y
  `docs/historia/brief_tres_correcciones.md` están **los tres cerrados**, aunque se lean como órdenes de
  trabajo abiertas. Cada uno shipeó: la base mayorista en v18 (`a84a3e5`), el crudo de descubrimiento de la
  TAREA B en v19 (`10c0983`) y el precio en quiebre de la TAREA A en v20, y el trío truncamiento / auditoría /
  `surtido_makro` en `8569056`, `03e2189` y `a2b6775`. Se conservan porque registran qué evidencia forzó cada
  cambio, y cada uno abre ahora con un blockquote que lo dice. Leídos como pendientes, harían rehacer trabajo
  ya terminado. Dos ítems realmente abiertos les sobreviven: `surtido_makro` se midió redundante y
  deliberadamente **no** se borró (revisar a los 30 días, o cuando el alcance salga de abarrotes), y los
  umbrales 6, 10, 12, 13 y 15 no tienen auditoría bajo la fórmula mayorista posterior a v18.
- `CLAUDE.md` está **trackeado por git**: cada edición entra al historial y al diff de un PR. Tratalo como
  fuente, no como scratch. `.gitignore` cubre `.env`, `data/`, `logs/`, `graphify-out/`, `.vscode/`, los
  artefactos de build, y las dos salidas de la captura logística (`ops/.mk_capture_profile/`,
  `ops/captura_mk_shipping.json`) — la segunda por los identificadores de sesión que lleva en el cuerpo.
- `graphify-out/` (gitignored) puede contener un grafo de conocimiento generado de este repo — útil para
  orientarse cuando está, pero el archivo del motor es siempre la fuente de verdad. Hoy no está en el árbol.
- **`test_propiedades_corrida.py` se archivó el 2026-08-30, no se reparó.** Estaba clavado a
  `run_20260822_020027`, borrada del disco, así que se salteaba a sí mismo y reportaba verde sin afirmar nada.
  Reconstruir esa cobertura contra una corrida sobreviviente (`run_20260826_021034`, la corrida contra la que
  está escrito `docs/columnas.md`) es un test *nuevo*, con su propio razonamiento sobre qué invariantes siguen
  valiendo bajo otro alcance (`--categoria /431/`) — no un re-apuntado del archivado. Ver
  `tests/historia/regresion_makro_plazavea/LEEME.md`.
- `docs/historia/decisiones_1.1.0.md` — inventario cerrado de lo que entregó 1.1.0: la mecánica del bi-precio,
  las 22 columnas, bugs conocidos, criterio de aceptación y alcance. Es dueño de esos números y definiciones;
  no los repitas acá, donde las dos copias se separarían con el tiempo. Consultalo antes de proponer cambios —
  pero leelo como el registro de *esa* versión, no como la hoja de ruta actual. **Sus partes superadas están
  anotadas en el lugar** (un blockquote en cada una): la fórmula mayorista en §1 y §5, la afirmación sobre
  surtido en §2, el conteo de 78 columnas y el enum `biprecio_status` en §5, y `--categorias` en §12. **§11 no
  está anotada y debería estarlo**: su "resuelta en todo el rango observado (2 a 24)" se midió bajo la fórmula
  previa a v18, así que registra lo que se verificó entonces, no lo que está verificado ahora — ver "el precio
  mayorista se reconstruye". Su tabla de bugs de §9 tampoco está **del todo** saldada: los bugs del lado del
  colector están arreglados, y los del lado de las sondas (v1/v4 apuntando a `mk_scraping_engine_0.1.0.py`,
  v2/v3 a `v1.py`, el renombre de la sonda) hoy son irrelevantes más que arreglados — esas cinco sondas se
  archivaron en `tests/historia/sondas_makro_plazavea/` el 2026-08-30 y ya no corren. Si alguna vez se revive
  una, su bug vuelve con ella.
- `docs/historia/contradicciones.md` — la auditoría detrás de estas correcciones, con su sección
  `## Resoluciones`. Leelo antes de volver a agregar cualquier cosa que este archivo solía decir.