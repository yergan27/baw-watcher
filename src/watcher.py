"""
baw-watcher: daemon que vigila el monitor trifásico BAW 24/7.

Corre como systemd unit en peppygate (Debian) — un loop infinito que:
  1. Polea el BAW por LAN (tinytuya) y completa fases por cloud.
  2. Detecta transiciones de los bits "críticos" del DP fault (corte,
     sobre/subtensión, fuga, sobrecorriente, sobrecalentamiento, etc.).
  3. Dispara alertas multi-canal (Telegram + WhatsApp) cuando un fault
     se enciende, y otra cuando se apaga ("recuperado").
  4. Aplica debounce: una alerta por fault por ventana de re-aviso,
     así si una sobretensión persiste 2 horas no nos manda 3600 mensajes.

El servicio NO depende de peppysoft — corre solo en peppygate aunque
peppy esté apagado.
"""
from __future__ import annotations

import logging
import signal
import time
from dataclasses import dataclass
from typing import Callable

from baw_state import BAWState, FAULT_BITS, CRITICAL_FAULT_BITS, parse_state
from notifier import Notifier

log = logging.getLogger(__name__)


# ── Detector con debounce ─────────────────────────────────────────────

@dataclass
class FaultEvent:
    """Una alarma activa que ya notificamos al menos una vez."""
    bit: int
    name: str
    first_seen_at: float
    last_notified_at: float
    notify_count: int


class FaultTracker:
    """Memoria de qué faults críticos están activos AHORA y cuándo
    avisamos por última vez. Ante una transición OFF→ON manda alerta
    de "ALARMA"; ante ON→OFF manda "recuperada"; si una alarma persiste
    más que `repeat_after_s`, vuelve a avisar (para que no se olvide
    si la primera notificación pasó desapercibida).
    """

    def __init__(self, repeat_after_s: float = 30 * 60):
        self.repeat_after_s = repeat_after_s
        self._active: dict[int, FaultEvent] = {}

    def update(self, bitmap: int, now: float) -> tuple[list[FaultEvent],
                                                         list[FaultEvent]]:
        """Devuelve (a_notificar_nuevas, a_notificar_recuperadas).

        Una alarma cuenta como "nueva" si:
          - acaba de prenderse (no estaba en `_active`), o
          - sigue prendida pero hace más de `repeat_after_s` que no
            avisamos (re-notify recordatorio).
        """
        bits_on = {b for b in CRITICAL_FAULT_BITS if bitmap & (1 << b)}

        a_notificar = []
        for bit in bits_on:
            if bit in self._active:
                ev = self._active[bit]
                # Re-notificar si pasó la ventana
                if now - ev.last_notified_at >= self.repeat_after_s:
                    ev.last_notified_at = now
                    ev.notify_count += 1
                    a_notificar.append(ev)
            else:
                ev = FaultEvent(
                    bit=bit, name=FAULT_BITS[bit],
                    first_seen_at=now, last_notified_at=now,
                    notify_count=1,
                )
                self._active[bit] = ev
                a_notificar.append(ev)

        # Recuperadas: las que estaban activas pero ya no
        recuperadas = []
        for bit in list(self._active.keys()):
            if bit not in bits_on:
                recuperadas.append(self._active.pop(bit))

        return a_notificar, recuperadas


# ── Mensajes ──────────────────────────────────────────────────────────

def _mensaje_alarma(state: BAWState, fault: FaultEvent) -> tuple[str, str]:
    titulo = f"⚠️ {fault.name}"
    if fault.notify_count > 1:
        titulo += f" (recordatorio #{fault.notify_count})"
    cuerpo = (
        f"BAW reportó: {fault.name}\n"
        f"Fase R: {state.phase_a.voltage:.0f}V {state.phase_a.current:.1f}A "
        f"{state.phase_a.power:.0f}W\n"
        f"Fase S: {state.phase_b.voltage:.0f}V {state.phase_b.current:.1f}A "
        f"{state.phase_b.power:.0f}W\n"
        f"Fase T: {state.phase_c.voltage:.0f}V {state.phase_c.current:.1f}A "
        f"{state.phase_c.power:.0f}W\n"
        f"Total: {state.total_power:.0f}W\n"
        f"Hora: {state.fetched_at.strftime('%H:%M:%S')}"
    )
    return titulo, cuerpo


def _mensaje_recuperado(state: BAWState, fault: FaultEvent) -> tuple[str, str]:
    duracion_s = state.fetched_at.timestamp() - fault.first_seen_at
    mins = int(duracion_s // 60)
    titulo = f"✅ Recuperado: {fault.name}"
    cuerpo = (
        f"BAW dejó de reportar: {fault.name}\n"
        f"Duración total de la alarma: {mins} min\n"
        f"Hora de recuperación: {state.fetched_at.strftime('%H:%M:%S')}"
    )
    return titulo, cuerpo


def _mensaje_offline(state: BAWState, first_seen_at: float,
                      notify_count: int) -> tuple[str, str]:
    """Alerta de que el BAW dejó de reportar. Causa más probable en este
    negocio: corte de luz total (el BAW pierde alimentación y se cae
    de la nube). Otras causas posibles: wifi caído, BAW desenchufado,
    cloud de Tuya con problemas."""
    dur_s = state.fetched_at.timestamp() - first_seen_at
    mins = int(dur_s // 60)
    secs = int(dur_s % 60)
    if notify_count > 1:
        titulo = f"⚠️ BAW sin respuesta (recordatorio #{notify_count})"
    else:
        titulo = "⚠️ BAW sin respuesta — posible corte de luz"
    cuerpo = (
        f"El monitor trifásico dejó de reportar a la nube.\n"
        f"Causa más probable: corte de luz total en el negocio.\n"
        f"Otras causas posibles: wifi caído, BAW desenchufado.\n"
        f"Tiempo sin respuesta: {mins} min {secs} s\n"
        f"Detalle técnico: {state.error or 'sin info'}\n"
        f"Hora: {state.fetched_at.strftime('%H:%M:%S')}"
    )
    return titulo, cuerpo


def _mensaje_offline_recuperado(state: BAWState,
                                  duracion_s: float) -> tuple[str, str]:
    mins = int(duracion_s // 60)
    secs = int(duracion_s % 60)
    titulo = "✅ BAW reconectado"
    cuerpo = (
        f"El monitor trifásico volvió a reportar a la nube.\n"
        f"Duración del incidente: {mins} min {secs} s\n"
        f"Hora de recuperación: {state.fetched_at.strftime('%H:%M:%S')}"
    )
    return titulo, cuerpo


# ── Loop principal ────────────────────────────────────────────────────

class Watcher:
    """El loop principal del daemon. `fetch_fn` es una callable
    sin argumentos que devuelve un BAWState — así inyectamos el cliente
    real en producción y un mock en los tests.
    """

    def __init__(self, fetch_fn: Callable[[], BAWState],
                 notifier: Notifier,
                 poll_interval_s: float = 5.0,
                 repeat_after_s: float = 30 * 60,
                 offline_alert_after_ticks: int = 6):
        self.fetch_fn = fetch_fn
        self.notifier = notifier
        self.poll_interval_s = poll_interval_s
        self.repeat_after_s = repeat_after_s
        self.offline_alert_after_ticks = max(1, int(offline_alert_after_ticks))
        self.tracker = FaultTracker(repeat_after_s=repeat_after_s)
        self._stop = False
        # Tracking del estado offline para alertar "posible corte de luz"
        self._consecutive_offline = 0
        self._offline_first_seen_at: float | None = None
        self._offline_last_notified_at: float | None = None
        self._offline_notify_count = 0

    def request_stop(self, *_):
        log.info("watcher: stop solicitado")
        self._stop = True

    def _handle_offline(self, state: BAWState, now: float) -> None:
        self._consecutive_offline += 1
        if self._offline_first_seen_at is None:
            self._offline_first_seen_at = now
        # Loggeamos en ticks de progreso para no inundar journalctl
        if self._consecutive_offline in (1, 6, 60, 600):
            log.warning("BAW offline (#%d): %s",
                        self._consecutive_offline, state.error)

        if self._consecutive_offline < self.offline_alert_after_ticks:
            return

        if self._offline_last_notified_at is None:
            self._offline_notify_count = 1
            log.warning("ALARMA OFFLINE: BAW desconectado tras %d ticks",
                        self._consecutive_offline)
            titulo, cuerpo = _mensaje_offline(
                state, self._offline_first_seen_at,
                self._offline_notify_count,
            )
            self.notifier.send(titulo, cuerpo)
            self._offline_last_notified_at = now
        elif now - self._offline_last_notified_at >= self.repeat_after_s:
            self._offline_notify_count += 1
            log.warning("RECORDATORIO OFFLINE #%d",
                        self._offline_notify_count)
            titulo, cuerpo = _mensaje_offline(
                state, self._offline_first_seen_at,
                self._offline_notify_count,
            )
            self.notifier.send(titulo, cuerpo)
            self._offline_last_notified_at = now

    def _handle_recovered_from_offline(self, state: BAWState,
                                         now: float) -> None:
        if self._consecutive_offline == 0:
            return
        alerta_emitida = self._offline_last_notified_at is not None
        log.info("BAW back online tras %d ticks offline (alerta_emitida=%s)",
                 self._consecutive_offline, alerta_emitida)
        if alerta_emitida:
            duracion_s = now - (self._offline_first_seen_at or now)
            titulo, cuerpo = _mensaje_offline_recuperado(state, duracion_s)
            self.notifier.send(titulo, cuerpo)
        self._consecutive_offline = 0
        self._offline_first_seen_at = None
        self._offline_last_notified_at = None
        self._offline_notify_count = 0

    def tick(self) -> BAWState:
        """Una iteración del loop. Expuesto para tests."""
        state = self.fetch_fn()
        now = state.fetched_at.timestamp()

        if not state.online:
            self._handle_offline(state, now)
            return state

        self._handle_recovered_from_offline(state, now)

        nuevas, recuperadas = self.tracker.update(state.fault_bitmap, now)

        for ev in nuevas:
            titulo, cuerpo = _mensaje_alarma(state, ev)
            log.warning("ALARMA bit=%d: %s", ev.bit, ev.name)
            self.notifier.send(titulo, cuerpo)
        for ev in recuperadas:
            titulo, cuerpo = _mensaje_recuperado(state, ev)
            log.info("RECUPERADA bit=%d: %s", ev.bit, ev.name)
            self.notifier.send(titulo, cuerpo)

        return state

    def run(self):
        log.info("watcher: arranca poll_interval=%.1fs critical_bits=%s",
                 self.poll_interval_s, sorted(CRITICAL_FAULT_BITS))
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)
        while not self._stop:
            try:
                self.tick()
            except Exception:
                log.exception("watcher: tick falló (continuamos)")
            # Sleep con resolución corta para reaccionar a SIGTERM rápido
            slept = 0.0
            while slept < self.poll_interval_s and not self._stop:
                time.sleep(min(0.5, self.poll_interval_s - slept))
                slept += 0.5
        log.info("watcher: stop limpio")
