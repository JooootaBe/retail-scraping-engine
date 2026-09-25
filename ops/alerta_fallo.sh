#!/usr/bin/env bash
# Se dispara via OnFailure= cuando la corrida diaria agoto su reintento.
#
# Dos canales a proposito: la notificacion de escritorio se pierde si a las
# 02:30 no hay sesion grafica abierta, y el archivo no. El archivo es el que
# vale; la notificacion es la comodidad.
set -uo pipefail

readonly LOGS=/home/jota/retail_scraping_engine/data/makro_plazavea/logs
mkdir -p "$LOGS"

estado=$(systemctl --user show motor-makro.service -p Result --value 2>/dev/null)
codigo=$(systemctl --user show motor-makro.service -p ExecMainStatus --value 2>/dev/null)

# Distinguir la alerta real de la prueba a mano.
#
# Corrido suelto para "ver si el aviso anda", este script antes gritaba que la
# corrida habia fallado mientras el cuerpo del mensaje decia result=success
# exit=0. Un avisador que puede mentir en la direccion de la alarma envenena
# todas las alarmas siguientes, asi que ahora consulta el estado real: si el
# servicio no esta en failed, se anuncia como prueba y NO ensucia fallas.log.
if [ "$estado" = "success" ] || [ -z "$estado" ]; then
    echo "PRUEBA del avisador: motor-makro.service NO esta en failed (result=${estado:-desconocido}, exit=${codigo:-?})."
    echo "No se escribio nada en fallas.log."
    if command -v notify-send >/dev/null 2>&1; then
        notify-send --urgency=low \
            "Motor Makro: prueba del avisador" \
            "Esto es una prueba, no una falla. La corrida NO fallo (result=${estado:-desconocido}, exit=${codigo:-?})." 2>/dev/null || true
    fi
    exit 0
fi

echo "$(date -Is) FALLO motor-makro.service | result=${estado} exit=${codigo:-?} | journalctl --user -u motor-makro -n 50" >> "$LOGS/fallas.log"

if command -v notify-send >/dev/null 2>&1; then
    notify-send --urgency=critical \
        "Motor Makro: la corrida diaria fallo" \
        "result=${estado}, exit=${codigo:-?}. Revisa: journalctl --user -u motor-makro" 2>/dev/null || true
fi
