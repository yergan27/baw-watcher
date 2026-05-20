"""
Ubicación del BAW en la red local.

Cuando la nube de Tuya deja de reportar el BAW, no alcanza para saber
QUÉ pasó: puede ser un corte de luz (el BAW se apagó) o solo una caída
de la conexión a la nube / internet (el BAW sigue encendido).

peppygate está en la misma red local que el BAW, así que podemos
verificar si el BAW está vivo ahí. El problema: la IP del BAW la asigna
el router por DHCP y **cambia sola** cada tanto. Por eso identificamos
al BAW por su **MAC** (que es fija), no por IP:

  1. Camino rápido: si la última IP conocida sigue aceptando una
     conexión TCP en el puerto Tuya (6668), el BAW está vivo — listo.
  2. Camino lento (si la IP cambió o no responde): barremos la red
     local con conexiones TCP cortas para refrescar la tabla ARP del
     sistema, y buscamos qué IP tiene ahora la MAC del BAW.

Si la MAC aparece en la tabla ARP → el BAW respondió → está encendido
y en la red. Si no aparece → coherente con un corte de luz.

Todo con sockets y lectura de `/proc/net/arp`: sin `ping`, sin tcpdump,
sin privilegios — funciona bajo el systemd endurecido del servicio.
"""
from __future__ import annotations

import logging
import socket
import time
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger(__name__)

# Puerto del protocolo Tuya LAN — el BAW lo tiene abierto siempre que
# esté alimentado y conectado a la red.
BAW_TUYA_PORT = 6668


# ── Chequeo directo de una IP ─────────────────────────────────────────

def baw_responde_en_lan(ip: str, port: int = BAW_TUYA_PORT,
                        timeout_s: float = 2.0) -> bool:
    """True si el BAW acepta una conexión TCP en su puerto Tuya — señal
    de que está encendido y conectado a la red local."""
    try:
        with socket.create_connection((ip, port), timeout=timeout_s):
            log.debug("BAW responde en LAN %s:%s", ip, port)
            return True
    except OSError as exc:
        log.debug("BAW no responde en %s:%s — %s", ip, port, exc)
        return False


# ── Búsqueda por MAC ──────────────────────────────────────────────────

def _normalizar_mac(mac: str) -> str:
    """MAC en minúsculas con ':' como separador."""
    return mac.strip().lower().replace("-", ":")


def _subnet_local() -> str | None:
    """Primeros tres octetos de la IP local (asume máscara /24).
    Devuelve None si no se puede determinar."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # No envía nada: solo fuerza al SO a elegir la ruta de
            # salida, y de ahí leemos la IP local.
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        return ip.rsplit(".", 1)[0]
    except OSError as exc:
        log.warning("no pude determinar la subred local: %s", exc)
        return None


def _tcp_touch(ip: str, port: int = BAW_TUYA_PORT,
               timeout_s: float = 0.5) -> None:
    """Intenta una conexión TCP a `ip` y la descarta. No nos importa si
    el puerto está abierto: con que el host esté vivo, el intento
    obliga al SO a resolver su MAC y la deja en la tabla ARP."""
    try:
        socket.create_connection((ip, port), timeout=timeout_s).close()
    except OSError:
        pass


def _barrer_subred(subnet: str, timeout_s: float = 0.5) -> None:
    """Toca todas las IPs de `subnet`.1-254 en paralelo para refrescar
    la tabla ARP del sistema."""
    ips = [f"{subnet}.{i}" for i in range(1, 255)]
    with ThreadPoolExecutor(max_workers=64) as pool:
        list(pool.map(lambda ip: _tcp_touch(ip, timeout_s=timeout_s), ips))
    # Pequeño respiro para que las entradas ARP terminen de asentarse.
    time.sleep(0.3)


def _leer_tabla_arp() -> str:
    """Contenido de /proc/net/arp (Linux). '' si no se puede leer."""
    try:
        with open("/proc/net/arp", "r", encoding="ascii", errors="replace") as f:
            return f.read()
    except OSError as exc:
        log.warning("no pude leer /proc/net/arp: %s", exc)
        return ""


def ip_de_mac_en_arp(tabla_arp: str, mac: str) -> str | None:
    """Parsea /proc/net/arp y devuelve la IP asociada a `mac`, o None.

    Formato de /proc/net/arp (con header en la primera línea):
        IP address  HW type  Flags  HW address  Mask  Device
    Flags '0x0' = entrada incompleta (el host no respondió) → se ignora.
    """
    mac = _normalizar_mac(mac)
    for linea in tabla_arp.splitlines()[1:]:   # saltear el header
        campos = linea.split()
        if len(campos) < 4:
            continue
        if campos[3].lower() == mac and campos[2] != "0x0":
            return campos[0]
    return None


def escanear_lan_por_mac(mac: str, subnet: str | None = None) -> str | None:
    """Barre la red local y devuelve la IP que tiene esa MAC ahora
    mismo, o None si el equipo no aparece (apagado / fuera de la red)."""
    mac = _normalizar_mac(mac)
    if subnet is None:
        subnet = _subnet_local()
    if subnet is None:
        log.warning("sin subred local — no puedo escanear por MAC")
        return None
    _barrer_subred(subnet)
    ip = ip_de_mac_en_arp(_leer_tabla_arp(), mac)
    if ip:
        log.debug("MAC %s ubicada en %s", mac, ip)
    return ip


class BAWLocator:
    """Encuentra al BAW en la LAN aunque DHCP le cambie la IP.

    Lo identifica por su MAC (fija). `ip_conocida` es una pista inicial
    opcional para el camino rápido; se actualiza sola si el BAW se
    movió de IP.
    """

    def __init__(self, mac: str, ip_conocida: str | None = None):
        self.mac = _normalizar_mac(mac)
        self.ip = ip_conocida or None

    def esta_vivo(self) -> bool:
        """True si el BAW está respondiendo en la red local ahora."""
        # Camino rápido: la última IP conocida sigue respondiendo.
        if self.ip and baw_responde_en_lan(self.ip):
            return True
        # Camino lento: DHCP pudo haberle cambiado la IP — lo buscamos
        # por su MAC en toda la red.
        nueva = escanear_lan_por_mac(self.mac)
        if nueva:
            if nueva != self.ip:
                log.info("BAW reubicado en la LAN: %s -> %s",
                         self.ip or "(sin IP previa)", nueva)
            self.ip = nueva
            return True
        log.info("BAW no encontrado en la LAN (MAC %s) — no está en la red",
                 self.mac)
        return False
