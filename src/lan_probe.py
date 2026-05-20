"""
Chequeo del BAW en la red local.

Cuando la nube de Tuya deja de reportar el BAW, no alcanza para saber
QUÉ pasó: puede ser un corte de luz (el BAW se apagó) o solo una caída
de la conexión a la nube / internet (el BAW sigue encendido).

peppygate está en la misma red local que el BAW, así que podemos
intentar conectarnos directo a su puerto Tuya (6668). Si el BAW acepta
la conexión, está encendido y con WiFi → el problema es la nube, no la
luz. Si no responde, es coherente con un corte de luz.

Usamos un connect TCP, no un ping ICMP: no necesita privilegios
(funciona bajo el systemd endurecido) y confirma que el servicio Tuya
del BAW está vivo, no solo que algo contesta en esa IP.
"""
from __future__ import annotations

import logging
import socket

log = logging.getLogger(__name__)

# Puerto del protocolo Tuya LAN — el BAW lo tiene abierto siempre que
# esté alimentado y conectado a la red.
BAW_TUYA_PORT = 6668


def baw_responde_en_lan(ip: str, port: int = BAW_TUYA_PORT,
                        timeout_s: float = 2.0) -> bool:
    """True si el BAW acepta una conexión TCP en su puerto Tuya — señal
    de que está encendido y conectado a la red local. False ante
    cualquier error de conexión (timeout, host inalcanzable, etc.)."""
    try:
        with socket.create_connection((ip, port), timeout=timeout_s):
            log.debug("BAW responde en LAN %s:%s", ip, port)
            return True
    except OSError as exc:
        log.debug("BAW no responde en LAN %s:%s — %s", ip, port, exc)
        return False
