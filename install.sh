#!/bin/bash
# Instalador del baw-watcher en peppygate.
# - Copia la unit a /etc/systemd/system/
# - Asegura el dir /etc/baw-watcher con permisos restrictivos para el .env
# - Habilita y arranca el servicio
#
# Uso (en peppygate): sudo ./install.sh
#
# El .env (con secretos) NO lo gestiona este script — el operador lo
# pega manualmente en /etc/baw-watcher/baw-watcher.env y le da
# permisos 0600.
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
    echo "Correr con sudo." >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

# Dir de config (con .env)
mkdir -p /etc/baw-watcher
chmod 0750 /etc/baw-watcher
chown root:peppygate /etc/baw-watcher

if [ ! -f /etc/baw-watcher/baw-watcher.env ]; then
    cat > /etc/baw-watcher/baw-watcher.env <<'EOF'
# Secretos del baw-watcher. Permisos 0640 root:peppygate — el daemon
# corre como peppygate y necesita poder leerlo.

# BAW por LAN (cliente tinytuya v3.5).
#   - DEVICE_ID y LOCAL_KEY se sacan del wizard de tinytuya (mismo
#     local_key que tiene peppysoft en gestion_app/.env).
#   - BAW_LAN_MAC identifica al BAW en la red aunque DHCP le cambie
#     la IP (caso ya visto: el router le rota la IP cada tanto).
#   - BAW_LAN_IP es solo una pista inicial opcional para arrancar
#     más rápido la primera vez.
TUYA_BAW_DEVICE_ID=
TUYA_BAW_LOCAL_KEY=
BAW_LAN_MAC=80:64:7C:9D:26:63
BAW_LAN_IP=

# Telegram (primary alerts)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_IDS=

# WhatsApp Business (secondary alerts) — mismo token que peppysoft
WA_TOKEN=
WA_PHONE_ID=
WA_ALERT_NUMBERS=

# Tuning
POLL_INTERVAL_S=5
REPEAT_AFTER_S=1800
# Ticks consecutivos sin respuesta antes de alertar desconexión.
# Con POLL_INTERVAL_S=5, 3 ticks ≈ 15 s.
OFFLINE_ALERT_AFTER_TICKS=3
# Segundos que tiene que durar una caída de la conexión de peppygate
# (DNS/internet) antes de avisar. Los parpadeos cortos son ruido y el
# BAW está bien igual, así que no generan alerta. Default 120 (2 min).
INTERNET_ALERT_AFTER_S=120
LOG_LEVEL=INFO
EOF
    chmod 0640 /etc/baw-watcher/baw-watcher.env
    chown root:peppygate /etc/baw-watcher/baw-watcher.env
    echo "Creado /etc/baw-watcher/baw-watcher.env vacío — completar y reiniciar."
fi

# Systemd unit
install -m 0644 "$REPO_DIR/baw-watcher.service" /etc/systemd/system/baw-watcher.service
systemctl daemon-reload
systemctl enable baw-watcher.service

echo "Listo. Asegurate de tener tinytuya instalado (dep del cliente LAN):"
echo "  sudo apt install -y python3-pip && sudo pip3 install --break-system-packages tinytuya"
echo "Después editar /etc/baw-watcher/baw-watcher.env con los secretos y:"
echo "  sudo systemctl restart baw-watcher"
echo "  sudo journalctl -u baw-watcher -f"
