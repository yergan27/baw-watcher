"""
Tests del watcher (sin tocar red real).

Mockeamos `fetch_fn` con states sintéticos y un notifier que registra
qué se mandó. Probamos:
  - Detección de transición OFF→ON de un fault crítico → alerta.
  - Detección de transición ON→OFF → mensaje de "recuperada".
  - Anti-spam: misma alarma dos ticks seguidos NO re-notifica.
  - Re-notificación tras el `repeat_after_s`.
  - Fault no-crítico (ej. desbalance) NO dispara alerta.
  - BAW offline con debounce: blip corto NO dispara; offline sostenido SÍ.
  - Recuperación tras corte sostenido emite mensaje "reconectado".
  - Recordatorio offline tras pasar la ventana `repeat_after_s`.
"""
import os
import sys
from datetime import datetime
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from baw_state import BAWState, FAULT_BITS, CRITICAL_FAULT_BITS, Phase  # noqa: E402
from notifier import MultiNotifier, Notifier  # noqa: E402
from watcher import FaultTracker, Watcher  # noqa: E402


class _CapturingNotifier(Notifier):
    name = "capture"

    def __init__(self):
        self.sent = []

    def send(self, titulo, cuerpo):
        self.sent.append((titulo, cuerpo))
        return True


def _state(bitmap=0, online=True, ts=None, error=None) -> BAWState:
    if isinstance(ts, (int, float)):
        ts = datetime.fromtimestamp(ts)
    return BAWState(
        online=online, relay_on=True,
        phase_a=Phase(voltage=220, current=1, power=220),
        phase_b=Phase(voltage=220, current=1, power=220),
        phase_c=Phase(voltage=220, current=1, power=220),
        total_power=660.0,
        fault_bitmap=bitmap,
        faults=[FAULT_BITS[b] for b in FAULT_BITS if bitmap & (1 << b)],
        fetched_at=ts or datetime.now(),
        error=error,
    )


# ── FaultTracker (debounce puro) ─────────────────────────────────────

class TestFaultTracker:
    def test_primer_fault_se_notifica(self):
        t = FaultTracker(repeat_after_s=1000)
        nuevas, recup = t.update(1 << 13, now=0.0)  # bit 13 = corte de red
        assert len(nuevas) == 1
        assert nuevas[0].bit == 13
        assert recup == []

    def test_fault_persistente_no_se_repite(self):
        # Tick 1: corte. Tick 2: sigue el corte → NO segunda alerta.
        t = FaultTracker(repeat_after_s=1000)
        t.update(1 << 13, now=0.0)
        nuevas, recup = t.update(1 << 13, now=2.0)
        assert nuevas == []
        assert recup == []

    def test_fault_resuelto_se_reporta(self):
        t = FaultTracker(repeat_after_s=1000)
        t.update(1 << 13, now=0.0)
        nuevas, recup = t.update(0, now=2.0)
        assert nuevas == []
        assert len(recup) == 1
        assert recup[0].bit == 13

    def test_repeat_tras_ventana(self):
        t = FaultTracker(repeat_after_s=10.0)
        t.update(1 << 13, now=0.0)
        nuevas_durante_ventana, _ = t.update(1 << 13, now=5.0)
        assert nuevas_durante_ventana == []
        nuevas_post_ventana, _ = t.update(1 << 13, now=11.0)
        assert len(nuevas_post_ventana) == 1
        assert nuevas_post_ventana[0].notify_count == 2

    def test_fault_no_critico_se_ignora(self):
        # Bit 9 = desbalance de corriente — NO está en CRITICAL_FAULT_BITS.
        assert 9 not in CRITICAL_FAULT_BITS
        t = FaultTracker(repeat_after_s=1000)
        nuevas, recup = t.update(1 << 9, now=0.0)
        assert nuevas == []
        assert recup == []

    def test_multiples_faults_simultaneos(self):
        # Corte de red + fuga de corriente → 2 alertas distintas.
        t = FaultTracker(repeat_after_s=1000)
        bitmap = (1 << 13) | (1 << 3)
        nuevas, _ = t.update(bitmap, now=0.0)
        assert {n.bit for n in nuevas} == {3, 13}


# ── Watcher end-to-end ───────────────────────────────────────────────

class TestWatcher:
    def test_arranque_limpio_no_dispara_nada(self):
        cap = _CapturingNotifier()
        states = iter([_state(bitmap=0)])
        w = Watcher(
            fetch_fn=lambda: next(states),
            notifier=cap, poll_interval_s=0,
        )
        w.tick()
        assert cap.sent == []

    def test_fault_critico_dispara_alerta(self):
        cap = _CapturingNotifier()
        s = _state(bitmap=1 << 13)  # corte de red
        w = Watcher(fetch_fn=lambda: s, notifier=cap, poll_interval_s=0)
        w.tick()
        assert len(cap.sent) == 1
        titulo, _ = cap.sent[0]
        assert "Corte de red" in titulo

    def test_fault_critico_no_se_repite_en_segundo_tick(self):
        cap = _CapturingNotifier()
        s = _state(bitmap=1 << 13)
        w = Watcher(fetch_fn=lambda: s, notifier=cap, poll_interval_s=0,
                    repeat_after_s=1000)
        w.tick(); w.tick(); w.tick()
        assert len(cap.sent) == 1   # solo la primera

    def test_recuperacion_dispara_segundo_mensaje(self):
        cap = _CapturingNotifier()
        secuencia = iter([_state(bitmap=1 << 13), _state(bitmap=0)])
        w = Watcher(
            fetch_fn=lambda: next(secuencia),
            notifier=cap, poll_interval_s=0,
        )
        w.tick(); w.tick()
        assert len(cap.sent) == 2
        assert "Corte de red" in cap.sent[0][0]
        assert "Recuperado" in cap.sent[1][0]

    def test_offline_blip_corto_no_dispara_alertas(self):
        # Default threshold = 6 ticks. 5 ticks offline = blip transitorio.
        cap = _CapturingNotifier()
        w = Watcher(
            fetch_fn=lambda: _state(online=False, error="cloud timeout"),
            notifier=cap, poll_interval_s=0,
        )
        for _ in range(5):
            w.tick()
        assert cap.sent == []

    def test_offline_sostenido_dispara_alerta_corte_de_luz(self):
        # Exactamente al tick #6 (threshold) sale la alerta "posible corte".
        cap = _CapturingNotifier()
        w = Watcher(
            fetch_fn=lambda: _state(online=False, error="cloud timeout"),
            notifier=cap, poll_interval_s=0,
            offline_alert_after_ticks=6,
        )
        for _ in range(6):
            w.tick()
        assert len(cap.sent) == 1
        titulo, cuerpo = cap.sent[0]
        assert "BAW sin respuesta" in titulo
        assert "corte de luz" in cuerpo

    def test_offline_threshold_configurable(self):
        cap = _CapturingNotifier()
        w = Watcher(
            fetch_fn=lambda: _state(online=False),
            notifier=cap, poll_interval_s=0,
            offline_alert_after_ticks=2,
        )
        w.tick()
        assert cap.sent == []
        w.tick()
        assert len(cap.sent) == 1

    def test_offline_sostenido_no_repite_dentro_de_ventana(self):
        # Una vez emitida la alerta, ticks adicionales offline NO repiten
        # hasta que pase `repeat_after_s`.
        cap = _CapturingNotifier()
        base_ts = 1_700_000_000.0  # 2023 — datetime.fromtimestamp(0) falla en Windows
        states = [_state(online=False, ts=base_ts + i) for i in range(20)]
        it = iter(states)
        w = Watcher(
            fetch_fn=lambda: next(it),
            notifier=cap, poll_interval_s=0,
            repeat_after_s=600.0,
            offline_alert_after_ticks=3,
        )
        for _ in range(20):
            w.tick()
        assert len(cap.sent) == 1

    def test_recordatorio_offline_tras_ventana(self):
        # Tick 1: t=0 offline. Tick 2: t=10 offline → alerta (threshold=2).
        # Tick 3..N: offline cada 10s. En t=620 (10s > 600s desde alerta)
        # debe salir el recordatorio.
        cap = _CapturingNotifier()
        base_ts = 1_700_000_000.0
        timestamps = [base_ts + 10.0 * i for i in range(70)]
        it = iter(_state(online=False, ts=t) for t in timestamps)
        w = Watcher(
            fetch_fn=lambda: next(it),
            notifier=cap, poll_interval_s=0,
            repeat_after_s=600.0,
            offline_alert_after_ticks=2,
        )
        for _ in range(70):
            w.tick()
        # Alerta inicial en tick 2 (t=10) + recordatorios cada 600s.
        # Ticks: 2 (t=10, alerta #1), 62 (t=610, alerta #2 porque 610-10=600).
        # Para ser robusto, al menos 2 alertas.
        assert len(cap.sent) >= 2
        assert "recordatorio" in cap.sent[1][0].lower()

    def test_blip_offline_y_vuelta_sin_alerta_es_silencioso(self):
        # 3 ticks offline (debajo threshold=6) + tick online = sin mensajes.
        cap = _CapturingNotifier()
        seq = [_state(online=False)] * 3 + [_state(online=True, bitmap=0)]
        it = iter(seq)
        w = Watcher(
            fetch_fn=lambda: next(it),
            notifier=cap, poll_interval_s=0,
        )
        for _ in range(4):
            w.tick()
        assert cap.sent == []

    def test_offline_sostenido_y_recuperacion_emite_dos_mensajes(self):
        # 6 ticks offline (dispara alerta) + 1 tick online (dispara recuperación).
        cap = _CapturingNotifier()
        base_ts = 1_700_000_000.0
        seq = ([_state(online=False, ts=base_ts + i) for i in range(6)] +
               [_state(online=True, bitmap=0, ts=base_ts + 100)])
        it = iter(seq)
        w = Watcher(
            fetch_fn=lambda: next(it),
            notifier=cap, poll_interval_s=0,
            offline_alert_after_ticks=6,
        )
        for _ in range(7):
            w.tick()
        assert len(cap.sent) == 2
        assert "BAW sin respuesta" in cap.sent[0][0]
        assert "reconectado" in cap.sent[1][0].lower()

    def test_fault_no_critico_no_dispara_alerta(self):
        cap = _CapturingNotifier()
        # bit 9 = desbalance de corriente, no es crítico
        s = _state(bitmap=1 << 9)
        w = Watcher(fetch_fn=lambda: s, notifier=cap, poll_interval_s=0)
        w.tick()
        assert cap.sent == []


# ── MultiNotifier ─────────────────────────────────────────────────────

class TestMultiNotifier:
    def test_un_canal_ok_devuelve_true(self):
        ok_ch = MagicMock(); ok_ch.send.return_value = True; ok_ch.name = "a"
        bad_ch = MagicMock(); bad_ch.send.return_value = False; bad_ch.name = "b"
        m = MultiNotifier(channels=[ok_ch, bad_ch])
        assert m.send("t", "c") is True

    def test_todos_fallan_devuelve_false(self):
        a = MagicMock(); a.send.return_value = False; a.name = "a"
        b = MagicMock(); b.send.return_value = False; b.name = "b"
        m = MultiNotifier(channels=[a, b])
        assert m.send("t", "c") is False

    def test_excepcion_en_un_canal_no_aborta_los_otros(self):
        bad = MagicMock(); bad.send.side_effect = RuntimeError("boom"); bad.name = "bad"
        good = MagicMock(); good.send.return_value = True; good.name = "good"
        m = MultiNotifier(channels=[bad, good])
        assert m.send("t", "c") is True
        good.send.assert_called_once()
