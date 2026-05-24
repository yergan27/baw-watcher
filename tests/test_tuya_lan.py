"""
Tests del TuyaLanClient.

tinytuya no está en los reqs de dev: stubbeamos `sys.modules["tinytuya"]`
con un OutletDevice fake ANTES de importar tuya_lan, así los tests
corren en cualquier máquina sin la dep real.
"""
import sys
import types

import pytest


# ── Stub de tinytuya ─────────────────────────────────────────────────

class _FakeOutletDevice:
    """Captura la config con la que lo construyeron y devuelve lo que
    `status_response` indique. Por defecto, una respuesta normal con DPs
    del BAW (sin las fases — el firmware no las publica por LAN)."""

    last_instance: "_FakeOutletDevice | None" = None

    # Configurables desde los tests
    status_response: object = {
        "dps": {
            "1": 1234,            # total_forward_energy (kWh × 100)
            "9": 0,               # fault bitmap — sin alarmas
            "15": 5,              # leakage_current (mA)
            "16": True,           # relé prendido
            "103": 27,            # temperatura interna
        }
    }
    raise_on_status: BaseException | None = None

    def __init__(self, dev_id, address, local_key, version):
        self.dev_id = dev_id
        self.address = address
        self.local_key = local_key
        self.version = version
        self.persistent = False
        self.retry_limit = None
        self.timeout = None
        self.closed = False
        type(self).last_instance = self

    def set_socketPersistent(self, v):    # noqa: N802 (API de tinytuya)
        self.persistent = v

    def set_socketRetryLimit(self, v):    # noqa: N802
        self.retry_limit = v

    def set_socketTimeout(self, v):       # noqa: N802
        self.timeout = v

    def status(self):
        if type(self).raise_on_status is not None:
            raise type(self).raise_on_status
        return type(self).status_response

    def close(self):
        self.closed = True


_fake_tinytuya = types.ModuleType("tinytuya")
_fake_tinytuya.OutletDevice = _FakeOutletDevice
sys.modules["tinytuya"] = _fake_tinytuya


# Recién ahora importamos lo que depende de tinytuya
from tuya_lan import TuyaLanClient  # noqa: E402


# ── Locator fake ─────────────────────────────────────────────────────

class _FakeLocator:
    """Imita el BAWLocator real: si `vivo` es True, esta_vivo() devuelve
    True y deja la IP en `ip`. Si False, no actualiza la IP y devuelve
    False (igual que cuando el BAW no aparece en la LAN)."""

    def __init__(self, ip=None, vivo=True):
        self.ip = ip
        self.vivo = vivo
        self.llamadas = 0

    def esta_vivo(self):
        self.llamadas += 1
        return self.vivo


@pytest.fixture(autouse=True)
def _reset_fake_device():
    """Cada test arranca con el fake en estado limpio."""
    _FakeOutletDevice.last_instance = None
    _FakeOutletDevice.raise_on_status = None
    _FakeOutletDevice.status_response = {
        "dps": {"1": 1234, "9": 0, "15": 5, "16": True, "103": 27}
    }


# ── Tests ────────────────────────────────────────────────────────────

def test_fetch_exitoso_devuelve_baw_state_online():
    loc = _FakeLocator(ip="192.168.100.5", vivo=True)
    cli = TuyaLanClient(device_id="dev123", local_key="key123", locator=loc)
    st = cli.fetch_state()
    assert st.online is True
    assert st.total_energy_kwh == pytest.approx(12.34)
    assert st.relay_on is True
    assert st.temp_c == 27
    assert st.fault_bitmap == 0
    # Las fases NO vienen por LAN — confirmamos que quedan en 0 (default).
    assert st.phase_a.voltage == 0


def test_baw_no_responde_en_lan_marca_offline():
    loc = _FakeLocator(ip=None, vivo=False)
    cli = TuyaLanClient(device_id="dev123", local_key="key123", locator=loc)
    st = cli.fetch_state()
    assert st.online is False
    assert "no encontrado" in (st.error or "").lower()


def test_excepcion_de_tinytuya_marca_offline():
    _FakeOutletDevice.raise_on_status = TimeoutError("read timed out")
    loc = _FakeLocator(ip="192.168.100.5", vivo=True)
    cli = TuyaLanClient(device_id="dev123", local_key="key123", locator=loc)
    st = cli.fetch_state()
    assert st.online is False
    assert "timed out" in (st.error or "").lower()


def test_respuesta_sin_dps_marca_offline():
    _FakeOutletDevice.status_response = {"Error": "Network Error"}
    loc = _FakeLocator(ip="192.168.100.5", vivo=True)
    cli = TuyaLanClient(device_id="dev123", local_key="key123", locator=loc)
    st = cli.fetch_state()
    assert st.online is False
    assert "network error" in (st.error or "").lower()


def test_falta_de_credenciales_aborta():
    loc = _FakeLocator(ip="192.168.100.5", vivo=True)
    with pytest.raises(ValueError):
        TuyaLanClient(device_id="", local_key="key", locator=loc)
    with pytest.raises(ValueError):
        TuyaLanClient(device_id="dev", local_key="", locator=loc)


def test_reconstruye_el_socket_si_dhcp_cambia_la_ip():
    loc = _FakeLocator(ip="192.168.100.5", vivo=True)
    cli = TuyaLanClient(device_id="dev123", local_key="key123", locator=loc)
    cli.fetch_state()
    dev1 = _FakeOutletDevice.last_instance
    assert dev1.address == "192.168.100.5"
    assert dev1.closed is False

    # Simula DHCP nuevo: el locator ahora reporta otra IP
    loc.ip = "192.168.100.42"
    cli.fetch_state()
    dev2 = _FakeOutletDevice.last_instance
    assert dev2 is not dev1                 # se creó uno nuevo
    assert dev2.address == "192.168.100.42"
    assert dev1.closed is True              # el viejo se cerró


def test_reutiliza_el_socket_si_la_ip_no_cambia():
    loc = _FakeLocator(ip="192.168.100.5", vivo=True)
    cli = TuyaLanClient(device_id="dev123", local_key="key123", locator=loc)
    cli.fetch_state()
    cli.fetch_state()
    cli.fetch_state()
    # Igual instancia siempre — no rotamos el socket en cada poll.
    dev = _FakeOutletDevice.last_instance
    assert dev.persistent is True
    assert dev.closed is False


def test_recuperacion_despues_de_errores_resetea_contador(caplog):
    _FakeOutletDevice.raise_on_status = TimeoutError("boom")
    loc = _FakeLocator(ip="192.168.100.5", vivo=True)
    cli = TuyaLanClient(device_id="dev123", local_key="key123", locator=loc)
    cli.fetch_state()
    assert cli._consecutive_errors == 1
    _FakeOutletDevice.raise_on_status = None
    cli.fetch_state()
    assert cli._consecutive_errors == 0
