"""
Tests del chequeo del BAW en la red local.

No tocamos el BAW real: levantamos un socket de prueba en localhost
para simular "el equipo responde" y un puerto cerrado para "no
responde".
"""
import socket

from lan_probe import baw_responde_en_lan


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
    # Reservamos un puerto y lo cerramos: nadie escucha ahí.
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.close()
    assert baw_responde_en_lan("127.0.0.1", port=port,
                               timeout_s=1.0) is False


def test_no_responde_si_la_ip_es_inalcanzable():
    # 192.0.2.1 es TEST-NET-1 (RFC 5737): no enrutable → falla.
    assert baw_responde_en_lan("192.0.2.1", port=6668,
                               timeout_s=1.0) is False
