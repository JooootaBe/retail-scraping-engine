"""
`tests/historia/` es archivo: nada de acá corre.

Existe porque el archivo de regresión conserva su nombre original
(`test_propiedades_corrida.py`) y pytest recogería cualquier `test_*.py` que
encuentre bajo `tests/`. Recogerlo lo haría fallar en el import —a esta
profundidad su `parents[2]` cae en `tests/`, no en la raíz del repo— y una
suite en rojo por un archivo archivado no dice nada sobre el motor.

Renombrarlo para esconderlo de pytest sería la otra salida, y es peor: el
nombre es lo que lo hace rastreable contra el historial de git y contra
`CHANGELOG.md`. Se prefiere declarar la carpeta entera como no-recolectable
y dejar el archivo tal como se archivó.
"""

collect_ignore_glob = ["*"]
