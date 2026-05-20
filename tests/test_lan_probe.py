"""
Tests de la ubicación del BAW en la red local.

No tocamos la red real: para el chequeo directo levantamos un socket
de prueba en localhost; para el parseo de ARP y el BAWLocator usamos
datos sintéticos y monkeypatch.
"""
import socket

import pytest

import lan_probe
from lan_probe import (BAWLocator, _normalizar_mac, _subnet_local,
                       baw_responde_en_lan, ip_de_mac_en_arp)


# ── Chequeo directo de una IP ─────────────────────────────────────────

def test_responde_si_el_puerto_esta_abierto():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert baw_responde_en_lan("127.0.0.1", port=port,
                                   timeout_s=2.0) is True
    finally:
        srv.close()


def test_no_responde_si_el_puerto_esta_cerrado():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.close()
    assert baw_responde_en_lan("127.0.0.1", port=port,
                               timeout_s=1.0) is False


def test_no_responde_si_la_ip_es_inalcanzable():
    # 192.0.2.1 es TEST-NET-1 (RFC 5737): no enrutable.
    assert baw_responde_en_lan("192.0.2.1", port=6668,
                               timeout_s=1.0) is False


# ── Parseo de la tabla ARP ────────────────────────────────────────────

_ARP_SAMPLE = (
    "IP address       HW type     Flags       HW address            Mask     Device\n"
    "192.168.100.5    0x1         0x2         80:64:7c:9d:26:63     *        enp0s25\n"
    "192.168.100.13   0x1         0x2         0c:9a:3c:53:d8:b0     *        enp0s25\n"
    "192.168.100.99   0x1         0x0         00:00:00:00:00:00     *        enp0s25\n"
)


def test_ip_de_mac_encuentra_la_ip():
    assert ip_de_mac_en_arp(_ARP_SAMPLE, "80:64:7c:9d:26:63") == "192.168.100.5"


def test_ip_de_mac_normaliza_mayusculas_y_guiones():
    assert ip_de_mac_en_arp(_ARP_SAMPLE, "80-64-7C-9D-26-63") == "192.168.100.5"


def test_ip_de_mac_devuelve_none_si_no_esta():
    assert ip_de_mac_en_arp(_ARP_SAMPLE, "aa:bb:cc:dd:ee:ff") is None


def test_ip_de_mac_ignora_entradas_incompletas():
    # Flag 0x0 = entrada incompleta (el host no respondió) → no sirve.
    assert ip_de_mac_en_arp(_ARP_SAMPLE, "00:00:00:00:00:00") is None


def test_ip_de_mac_tabla_vacia():
    assert ip_de_mac_en_arp("", "80:64:7c:9d:26:63") is None


# ── Utilidades ────────────────────────────────────────────────────────

def test_normalizar_mac():
    assert _normalizar_mac("80-64-7C-9D-26-63") == "80:64:7c:9d:26:63"


def test_subnet_local_devuelve_tres_octetos_o_none():
    s = _subnet_local()
    assert s is None or len(s.split(".")) == 3


# ── BAWLocator ────────────────────────────────────────────────────────

def test_locator_camino_rapido_no_escanea_si_la_ip_responde(monkeypatch):
    monkeypatch.setattr(lan_probe, "baw_responde_en_lan", lambda ip: True)
    monkeypatch.setattr(lan_probe, "escanear_lan_por_mac",
                        lambda mac: pytest.fail("no debió escanear"))
    loc = BAWLocator("80:64:7c:9d:26:63", ip_conocida="192.168.100.5")
    assert loc.esta_vivo() is True


def test_locator_reescanea_y_actualiza_la_ip_si_cambio(monkeypatch):
    monkeypatch.setattr(lan_probe, "baw_responde_en_lan", lambda ip: False)
    monkeypatch.setattr(lan_probe, "escanear_lan_por_mac",
                        lambda mac: "192.168.100.42")
    loc = BAWLocator("80:64:7c:9d:26:63", ip_conocida="192.168.100.5")
    assert loc.esta_vivo() is True
    assert loc.ip == "192.168.100.42"   # se actualizó sola


def test_locator_devuelve_false_si_no_encuentra_el_baw(monkeypatch):
    monkeypatch.setattr(lan_probe, "baw_responde_en_lan", lambda ip: False)
    monkeypatch.setattr(lan_probe, "escanear_lan_por_mac", lambda mac: None)
    loc = BAWLocator("80:64:7c:9d:26:63", ip_conocida="192.168.100.5")
    assert loc.esta_vivo() is False


def test_locator_sin_ip_inicial_escanea_directo(monkeypatch):
    monkeypatch.setattr(lan_probe, "baw_responde_en_lan",
                        lambda ip: pytest.fail("sin IP previa no debió chequear"))
    monkeypatch.setattr(lan_probe, "escanear_lan_por_mac",
                        lambda mac: "192.168.100.7")
    loc = BAWLocator("80:64:7C:9D:26:63")
    assert loc.esta_vivo() is True
    assert loc.ip == "192.168.100.7"
