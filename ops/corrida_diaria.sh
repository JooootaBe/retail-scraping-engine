#!/usr/bin/env bash
# Envoltorio de la corrida diaria del motor makro_plazavea.
#
# Existe para que la unidad de systemd no tenga logica adentro y para que la
# corrida automatica y la corrida a mano sean literalmente el mismo comando.
#
#   ops/corrida_diaria.sh                          # la corrida diaria canonica
#   ops/corrida_diaria.sh --dry-run --skus 10012716  # prueba del envoltorio, no escribe nada
#
# Sin argumentos usa el alcance diario. Con argumentos los pasa tal cual al
# motor, que es lo que hace verificable este archivo sin esperar 1,6 h.
set -uo pipefail

# Ruta absoluta al interprete: retail_engine esta instalado editable SOLO en
# anaconda; /usr/bin/python3 no lo importa. Una unidad de systemd no hereda el
# PATH de la shell, asi que esto no es estilo — es si la corrida arranca o no.
readonly PYTHON=/home/jota/anaconda3/bin/python
readonly REPO=/home/jota/retail_scraping_engine

# El --canal por defecto del motor es "chrome" y Playwright lo busca en el PATH.
export PATH=/usr/local/bin:/usr/bin:/bin

# El alcance diario: abarrotes, con auditoria mayorista. Cambiarlo cambia la
# serie temporal — no lo edites sin saber que las corridas viejas midieron otra
# cosa (ver "el alcance se elige, no se hereda del arbol" en CLAUDE.md).
readonly ALCANCE_DIARIO=(--categoria "/431/" --auditoria-mayorista 10)

cd "$REPO" || exit 2

readonly LOGS="$REPO/data/makro_plazavea/logs"
mkdir -p "$LOGS"
readonly LOG="$LOGS/$(date +%Y%m%d_%H%M%S).log"
readonly LOCK="$REPO/data/makro_plazavea/.corrida.lock"

if [ "$#" -gt 0 ]; then
    argumentos=("$@")
else
    argumentos=("${ALCANCE_DIARIO[@]}")
fi

# flock no bloqueante: si la corrida de ayer sigue viva (paso: 12,6 h el
# 2026-08-29), la de hoy se retira sin tocar nada en vez de medir en paralelo
# contra el mismo storefront.
exec 9>"$LOCK"
if ! flock -n 9; then
    echo "[corrida_diaria] ya hay una corrida en curso (lock: $LOCK); esta no arranca" | tee -a "$LOG"
    exit 0
fi

echo "[corrida_diaria] $(date -Is) inicio | ${argumentos[*]}" | tee -a "$LOG"

"$PYTHON" -m retail_engine.collectors.makro_plazavea "${argumentos[@]}" 2>&1 | tee -a "$LOG"
codigo=${PIPESTATUS[0]}

echo "[corrida_diaria] $(date -Is) fin | codigo de salida: $codigo" | tee -a "$LOG"

# El codigo de salida del motor es un contrato (evaluar_corrida): 0 completa,
# 1 medicion/stock cortados por presupuesto, 2 sin Playwright o argumento malo,
# 130 Ctrl-C. Si el envoltorio se lo traga, la automatizacion miente.
exit "$codigo"
