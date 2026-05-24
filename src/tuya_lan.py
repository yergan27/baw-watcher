"""
Cliente Tuya por LAN para el watcher — protocolo v3.5 con tinytuya.

Reemplaza a `tuya_cloud.py` como fuente de datos del watcher. La nube
de Tuya nos dejó de servir (el Trial Edition se agota y el endpoint
empieza a devolver `'Please upgrade to the official version: Your
quota of Trial Edition is used up.'`), así que el watcher pasa a leer
al BAW directo en la LAN del negocio — peppygate ya está en esa red y
no depende más de internet para vigilar.

Trade-off conocido: el firmware del BAW NO publica los DPs `phase_a/b/c`
(6, 7, 8) por LAN — solo por cloud. Para el watcher esto es aceptable:
solo se usaban como contexto en los mensajes de alarma (V/A/W de cada
fase); los faults críticos (DP 9) y todo lo demás (energía, temperatura,
relé, fuga) SÍ vienen por LAN. La alarma sigue diciendo qué falla es;
deja de incluir las lecturas instantáneas de fase. Aceptable.

La IP del BAW la cambia el router por DHCP (ya nos pasó), así que NO la
cableamos en el cliente: usamos `BAWLocator` (mismo que el lan_probe)
que identifica al BAW por su MAC y refresca la IP cuando hace falta.
"""
from __future__ import annotations

import logging
import threading

import tinytuya

from baw_state import BAWState, parse_state
from lan_probe import BAWLocator

log = logging.getLogger(__name__)


class TuyaLanClient:
    """Cliente tinytuya v3.5 contra el BAW en la LAN.

    `locator` es un `BAWLocator` que sabe encontrar al BAW por MAC. Si
    DHCP le cambió la IP, el cliente reconstruye la conexión tinytuya
    apuntando a la IP nueva — sin esto, después de un reinicio del
    router el watcher quedaría intentando para siempre contra la IP
    vieja.
    """

    def __init__(self, device_id: str, local_key: str, locator: BAWLocator,
                 version: float = 3.5,
                 socket_timeout_s: float = 3.0,
                 socket_retry_limit: int = 2):
        if not (device_id and local_key):
            raise ValueError("device_id y local_key son requeridos")
        self.device_id = device_id
        self.local_key = local_key
        self.locator = locator
        self.version = version
        self.socket_timeout_s = socket_timeout_s
        self.socket_retry_limit = socket_retry_limit
        self._dev: tinytuya.OutletDevice | None = None
        self._dev_ip: str | None = None
        self._lock = threading.Lock()
        self._consecutive_errors = 0
        log.info("TuyaLanClient init device=%s version=%s", device_id, version)

    def _ensure_device(self) -> tinytuya.OutletDevice | None:
        """Devuelve un OutletDevice apuntando a la IP actual del BAW.

        Si la IP cambió respecto del último fetch, cierra el socket viejo
        y construye uno nuevo. Si el BAW no aparece en la LAN, devuelve
        None — el caller emite BAWState(online=False).
        """
        ip = self.locator.ip
        if not ip or not self.locator.esta_vivo():
            return None
        ip = self.locator.ip
        if self._dev is None or self._dev_ip != ip:
            if self._dev is not None:
                try:
                    self._dev.close()
                except Exception:
                    pass
            self._dev = tinytuya.OutletDevice(
                dev_id=self.device_id, address=ip,
                local_key=self.local_key, version=self.version,
            )
            self._dev.set_socketPersistent(True)
            self._dev.set_socketRetryLimit(self.socket_retry_limit)
            self._dev.set_socketTimeout(self.socket_timeout_s)
            self._dev_ip = ip
            log.info("TuyaLanClient apuntando a IP=%s", ip)
        return self._dev

    def _registrar_error(self, msg: str) -> None:
        self._consecutive_errors += 1
        if self._consecutive_errors == 1 or self._consecutive_errors % 30 == 0:
            log.warning("BAW LAN fetch falló (#%d): %s",
                        self._consecutive_errors, msg)

    def fetch_state(self) -> BAWState:
        with self._lock:
            dev = self._ensure_device()
            if dev is None:
                self._registrar_error("BAW no encontrado en la LAN")
                return BAWState(
                    online=False,
                    error="LAN: BAW no encontrado (posible corte de luz o WiFi caído)",
                )
            try:
                data = dev.status()
            except Exception as exc:
                self._registrar_error(str(exc))
                return BAWState(online=False, error=f"LAN: {exc}")

        if not isinstance(data, dict) or "dps" not in data:
            err = (data.get("Error") if isinstance(data, dict)
                   else f"respuesta inesperada: {data!r}")
            self._registrar_error(str(err))
            return BAWState(online=False, error=f"LAN: {err}")

        if self._consecutive_errors > 0:
            log.info("BAW LAN recuperado tras %d errores",
                     self._consecutive_errors)
            self._consecutive_errors = 0
        return parse_state(data["dps"])
