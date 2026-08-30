# Regresión archivada — makro_plazavea

Un test de regresión, archivado el **2026-08-30**. **No corre y no se mantiene.** Estaba en
`tests/makro_plazavea/`, donde era uno de los siete vivos.

| archivo | qué probaba |
|---|---|
| `test_propiedades_corrida.py` | cuatro invariantes de TAREA A sobre las 3.174 filas de `run_20260822_020027`, recalculadas desde su `raw.jsonl.gz` |

## Por qué se archiva

**Su corrida de referencia ya no existe en disco.** El test estaba clavado a
`data/makro_plazavea/run_20260822_020027`, y `data/` está en `.gitignore`: la corrida se borró y no
se puede recuperar del repositorio. Lo que hace el test cuando no la encuentra es saltearse y salir
con código 0:

```
$ python3 test_propiedades_corrida.py
SALTEADO: no está data/makro_plazavea/run_20260822_020027
exit=0
```

Eso es lo que lo condena. **No fallaba: reportaba verde sin afirmar nada.** Nueve de las 73 funciones
`test_` que la suite decía tener no estaban protegiendo el motor de nada, y contaban igual en el
número. Un test que no puede fallar es peor que un test ausente, porque el ausente se nota.

Su etapa terminó cuando se borró esa corrida. Lo honesto es archivarlo, no dejarlo verde.

## Por qué no se re-apunta

Se podría apuntarlo a una corrida que sí exista —`run_20260826_021034` es la candidata obvia, es
contra la que está escrito `docs/columnas.md`—, y eso **no es este cambio**. Aquella corrida tiene
otro alcance (`--categoria /431/`, abarrotes) y otra época del motor, así que antes hay que decidir
cuáles de las cuatro invariantes siguen valiendo ahí y cuáles solo valían para el catálogo completo
de agosto. Eso es escribir un test nuevo con su propio razonamiento, no cambiar una ruta. Mientras
tanto, el archivo queda acá con lo que sí sabemos: qué probaba y por qué dejó de probarlo.

## Por qué no se borra

Registra **cómo se demostró** que la lógica de precio en quiebre (v20, TAREA A) era correcta sobre
una corrida real y no solo sobre filas armadas a mano. Su parte más valiosa es el control negativo:
recalcula las mismas filas ignorando `price_origin` y muestra que así salen 82 de 82 publicadas por
construcción aritmética. Ese razonamiento es el molde de cualquier reemplazo, y no está escrito en
ningún otro lado.

Las filas armadas a mano que cubren las seis ramas de `price_origin` **siguen vivas** en
`tests/makro_plazavea/test_precio_en_quiebre.py`. Lo que se perdió al archivar esto es la
verificación sobre una corrida real, no la cobertura de las reglas.

## Por qué hay un `conftest.py` en `tests/historia/`

El archivo conserva su nombre original, y pytest recoge cualquier `test_*.py` bajo `tests/`. A esta
profundidad su `RAIZ = parents[2]` cae en `tests/` en vez de en la raíz del repo, así que al
recogerlo falla en el import con `ModuleNotFoundError: No module named 'retail_engine'` — el mismo
modo de fallo que `CLAUDE.md` documenta y que ya rompió la suite dos veces. `tests/historia/conftest.py`
declara la carpeta entera como no-recolectable para que un archivo archivado no ponga en rojo a los
seis vivos. Verificado: con el conftest, `pytest tests/ -q` da 64 pasados; sin él, la recolección se
interrumpe con un error.

Renombrarlo para esconderlo de pytest era la otra salida y es peor: el nombre es lo que lo hace
rastreable contra el historial de git.
