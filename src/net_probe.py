"""
Chequeo de conectividad a internet de peppygate — sin depender de DNS.

Cuando el watcher no logra consultar la nube de Tuya, hay que saber de
quién es el problema:

  - Si el BAW perdió la nube → es un tema del BAW o de la nube de Tuya.
  - Si peppygate se quedó sin internet → no es un problema eléctrico ni
    del BAW; es la conexión de peppygate. NO hay que alarmar al dueño
    como si fuera un corte de luz.

Este módulo responde "¿peppygate tiene salida a internet?" conectándose
por TCP a IPs públicas conocidas (Cloudflare 1.1.1.1, Google 8.8.8.8).
Usa IPs fijas a propósito: así el chequeo NO necesita DNS. Eso importa
porque la causa más común de que peppygate pierda la nube es,
justamente, que se le caiga la resolución de nombres (DNS) — y un
chequeo que dependiera de DNS no podría distinguir ese caso.

Todo con sockets de la stdlib: sin dependencias, sin privilegios.
"""
from __future__ import annotations

import logging
import socket

log = logging.getLogger(__name__)

# (IP, puerto) de servicios públicos que están siempre arriba. Por IP
# fija — sin DNS de por medio. Los puertos 443 (HTTPS) y 53 (DNS) casi
# nunca están bloqueados por un router doméstico.
DESTINOS_INTERNET = (
    ("1.1.1.1", 443),   # Cloudflare
    ("8.8.8.8", 53),    # Google DNS
    ("1.1.1.1", 53),    # Cloudflare DNS
)


def hay_internet(destinos: tuple[tuple[str, int], ...] = DESTINOS_INTERNET,
                 timeout_s: float = 1.5) -> bool:
    """True si peppygate logra una conexión TCP a alguna IP pública.

    Prueba varios destinos: con que UNO responda, hay internet — y
    cortamos ahí. Como las direcciones son IPs fijas (sin DNS), un
    `False` acá significa de verdad "peppygate sin salida a internet",
    no "falló la resolución de nombres".
    """
    for ip, port in destinos:
        try:
            with socket.create_connection((ip, port), timeout=timeout_s):
                log.debug("internet OK via %s:%s", ip, port)
                return True
        except OSError as exc:
            log.debug("internet: %s:%s no respondió — %s", ip, port, exc)
    log.debug("internet: ningún destino respondió — peppygate sin salida")
    return False
