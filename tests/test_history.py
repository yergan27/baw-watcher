"""Tests del historial de eventos en SQLite."""
from history import HistoryStore


def test_record_y_recent_devuelve_lo_mas_nuevo_primero(tmp_path):
    h = HistoryStore(str(tmp_path / "h.db"))
    h.record("offline", "BAW sin respuesta", "detalle", ts=100.0)
    h.record("reconectado", "BAW reconectado", "detalle", ts=200.0)
    ev = h.recent()
    assert len(ev) == 2
    assert ev[0]["titulo"] == "BAW reconectado"
    assert ev[1]["kind"] == "offline"


def test_recent_respeta_el_limit(tmp_path):
    h = HistoryStore(str(tmp_path / "h.db"))
    for i in range(20):
        h.record("alarma", f"evento {i}", ts=float(i))
    assert len(h.recent(limit=5)) == 5


def test_persiste_entre_instancias(tmp_path):
    path = str(tmp_path / "h.db")
    HistoryStore(path).record("offline", "uno", ts=1.0)
    # Nueva instancia sobre la misma DB → el evento sigue ahí.
    assert len(HistoryStore(path).recent()) == 1


def test_resumen_texto_sin_eventos(tmp_path):
    h = HistoryStore(str(tmp_path / "h.db"))
    assert "no hay eventos" in h.resumen_texto().lower()


def test_resumen_texto_con_eventos(tmp_path):
    h = HistoryStore(str(tmp_path / "h.db"))
    h.record("offline", "BAW sin respuesta", ts=1_700_000_000.0)
    assert "BAW sin respuesta" in h.resumen_texto()


def test_store_deshabilitado_no_explota(tmp_path):
    # Directorio padre inexistente → no se puede abrir la DB.
    h = HistoryStore(str(tmp_path / "no" / "existe" / "h.db"))
    assert h._disabled is True
    h.record("offline", "x")              # no-op silencioso
    assert h.recent() == []
    assert "no hay eventos" in h.resumen_texto().lower()
