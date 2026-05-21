"""
Tests de net_probe — el chequeo de internet por IP fija (sin DNS).

No tocamos internet de verdad: levantamos un socket TCP local que hace
de "destino que responde", y usamos direcciones de la red de
documentación (192.0.2.0/24, RFC 5737, garantizada sin ruta) como
"destino que no responde".
"""
import os
import socket
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from net_probe import hay_internet  # noqa: E402

# Red de documentación (RFC 5737): nunca rutea — sirve de destino muerto.
_DESTINO_MUERTO = ("192.0.2.1", 443)


def _socket_que_escucha():
    """Abre un socket TCP en loopback y devuelve (sock, (ip, port))."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(8)
    return s, s.getsockname()


class TestHayInternet:
    def test_destino_que_responde_da_true(self):
        srv, addr = _socket_que_escucha()
        try:
            assert hay_internet(destinos=(addr,), timeout_s=2.0) is True
        finally:
            srv.close()

    def test_sin_destinos_alcanzables_da_false(self):
        destinos = (_DESTINO_MUERTO, ("192.0.2.2", 53))
        assert hay_internet(destinos=destinos, timeout_s=0.5) is False

    def test_corta_en_el_primer_destino_que_responde(self):
        # Con un destino alcanzable alcanza para devolver True.
        srv, addr = _socket_que_escucha()
        try:
            assert hay_internet(destinos=(addr, _DESTINO_MUERTO),
                                timeout_s=2.0) is True
        finally:
            srv.close()

    def test_segundo_destino_responde_aunque_el_primero_no(self):
        srv, addr = _socket_que_escucha()
        try:
            assert hay_internet(destinos=(_DESTINO_MUERTO, addr),
                                timeout_s=0.5) is True
        finally:
            srv.close()
