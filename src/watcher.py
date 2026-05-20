"""
baw-watcher: daemon que vigila el monitor trifásico BAW 24/7.

Corre como systemd unit en peppygate (Debian) — un loop infinito que:
  1. Polea el BAW por la nube de Tuya cada pocos segundos.
  2. Detecta transiciones de los bits "críticos" del DP fault (corte,
     sobre/subtensión, fuga, sobrecorriente, sobrecalentamiento, etc.).
  3. Detecta cuando el BAW deja de reportar. Si hay chequeo LAN
     configurado, distingue corte de luz (el BAW tampoco responde
     local) de caída de la nube (el BAW sí responde local).
  4. Dispara alertas multi-canal (Telegram + WhatsApp) cuando algo
     pasa, y otra cuando se normaliza ("recuperado").
  5. Aplica debounce: una alerta por evento por ventana de re-aviso,
     así si una sobretensión persiste 2 horas no nos manda 3600 mensajes.
  6. Registra cada evento en el historial (para el comando /historial).

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
                      notify_count: int,
                      lan_alive: bool | None) -> tuple[str, str]:
    """Alerta de que el BAW dejó de reportar a la nube.

    `lan_alive` es el resultado del chequeo del BAW en la red local y
    define QUÉ está pasando realmente:
      - True  → el BAW responde local: tiene luz y WiFi. Se cayó la
                conexión a la nube/internet. NO es corte de luz.
      - False → el BAW no responde ni por nube ni local: coherente con
                un corte de luz (o el BAW apagado/sin WiFi).
      - None  → no hay chequeo local configurado: no podemos afinar el
                diagnóstico.
    """
    dur_s = state.fetched_at.timestamp() - first_seen_at
    mins = int(dur_s // 60)
    secs = int(dur_s % 60)
    rec = f" (recordatorio #{notify_count})" if notify_count > 1 else ""

    if lan_alive is True:
        titulo = f"⚠️ BAW sin conexión a la nube{rec}"
        diag = (
            "El BAW SÍ responde en la red local: tiene luz y está "
            "conectado al WiFi.\n"
            "Se cortó la conexión con la nube de Tuya o con internet.\n"
            "NO parece un corte de luz — el monitor está funcionando."
        )
    elif lan_alive is False:
        titulo = f"🚨 Posible corte de luz{rec}"
        diag = (
            "El BAW no responde ni por la nube ni por la red local.\n"
            "Es coherente con un corte de luz en el negocio (o que el "
            "BAW se haya apagado o quedado sin WiFi)."
        )
    else:
        titulo = f"⚠️ BAW sin respuesta{rec}"
        diag = (
            "El monitor trifásico dejó de reportar a la nube.\n"
            "Causa más probable: corte de luz total en el negocio.\n"
            "Otras causas posibles: wifi caído, BAW desenchufado."
        )

    cuerpo = (
        f"{diag}\n"
        f"Tiempo sin reportar: {mins} min {secs} s\n"
        f"Detalle técnico: {state.error or 'sin info'}\n"
        f"Hora: {state.fetched_at.strftime('%H:%M:%S')}"
    )
    return titulo, cuerpo


def _fmt_estado(state: BAWState, encabezado: str) -> str:
    """Lectura del BAW formateada para el comando /estado."""
    faults = (f"Faults activos: {', '.join(state.faults)}"
              if state.faults else "Sin faults activos.")
    return (
        f"{encabezado}\n"
        f"Fase R: {state.phase_a.voltage:.0f} V · "
        f"{state.phase_a.current:.1f} A · {state.phase_a.power:.0f} W\n"
        f"Fase S: {state.phase_b.voltage:.0f} V · "
        f"{state.phase_b.current:.1f} A · {state.phase_b.power:.0f} W\n"
        f"Fase T: {state.phase_c.voltage:.0f} V · "
        f"{state.phase_c.current:.1f} A · {state.phase_c.power:.0f} W\n"
        f"Potencia total: {state.total_power:.0f} W\n"
        f"Temperatura: {state.temp_c:.0f} °C\n"
        f"{faults}\n"
        f"Hora de la lectura: {state.fetched_at.strftime('%H:%M:%S')}"
    )


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
                 offline_alert_after_ticks: int = 3,
                 lan_probe_fn: Callable[[], bool] | None = None,
                 history=None):
        """`lan_probe_fn` (opcional): callable que devuelve True si el
        BAW responde en la red local. Si se pasa, las alertas de
        desconexión distinguen 'corte de luz' de 'se cayó la nube'.
        `history` (opcional): un HistoryStore donde registrar eventos
        para el comando /historial.
        """
        self.fetch_fn = fetch_fn
        self.notifier = notifier
        self.poll_interval_s = poll_interval_s
        self.repeat_after_s = repeat_after_s
        self.offline_alert_after_ticks = max(1, int(offline_alert_after_ticks))
        self.lan_probe_fn = lan_probe_fn
        self.history = history
        self.tracker = FaultTracker(repeat_after_s=repeat_after_s)
        self._stop = False
        # Tracking del estado offline para alertar "posible corte de luz"
        self._consecutive_offline = 0
        self._offline_first_seen_at: float | None = None
        self._offline_last_notified_at: float | None = None
        self._offline_notify_count = 0
        # Última lectura vista (para el comando /estado) y última que
        # estuvo online (para mostrar datos aunque ahora esté caído).
        self.last_state: BAWState | None = None
        self.last_online_state: BAWState | None = None

    def request_stop(self, *_):
        log.info("watcher: stop solicitado")
        self._stop = True

    def _probe_lan(self) -> bool | None:
        """Chequea el BAW en la red local. Devuelve True/False si hay
        chequeo configurado, None si no — nunca propaga excepciones."""
        if self.lan_probe_fn is None:
            return None
        try:
            return bool(self.lan_probe_fn())
        except Exception:
            log.exception("watcher: chequeo LAN falló")
            return None

    def _registrar(self, kind: str, titulo: str, cuerpo: str,
                   now: float) -> None:
        """Guarda el evento en el historial si hay store configurado."""
        if self.history is not None:
            self.history.record(kind, titulo, cuerpo, ts=now)

    def estado_texto(self) -> str:
        """Texto de respuesta para el comando /estado de Telegram."""
        st = self.last_state
        if st is None:
            return ("Todavía no tengo lecturas del BAW. "
                    "Esperá unos segundos y volvé a probar.")
        if st.online:
            return _fmt_estado(st, "📊 Estado del BAW (ahora):")
        desde = st.fetched_at.strftime("%d/%m %H:%M:%S")
        txt = (f"⚠️ El BAW no está reportando a la nube (desde las "
               f"{desde}).\nDetalle: {st.error or 'sin info'}")
        if self.last_online_state is not None:
            txt += "\n\n" + _fmt_estado(
                self.last_online_state, "Última lectura conocida:")
        return txt

    def _handle_offline(self, state: BAWState, now: float) -> None:
        self._consecutive_offline += 1
        if self._offline_first_seen_at is None:
            self._offline_first_seen_at = now
        # Loggeamos en ticks de progreso para no inundar journalctl
        if self._consecutive_offline in (1, 3, 6, 60, 600):
            log.warning("BAW offline (#%d): %s",
                        self._consecutive_offline, state.error)

        if self._consecutive_offline < self.offline_alert_after_ticks:
            return

        primera = self._offline_last_notified_at is None
        if primera:
            self._offline_notify_count = 1
        elif now - self._offline_last_notified_at >= self.repeat_after_s:
            self._offline_notify_count += 1
        else:
            return  # ya alertamos y todavía no pasó la ventana de repaso

        # Recién ahora chequeamos la LAN: solo cuando vamos a notificar,
        # no en cada tick offline. Eso dice si es corte de luz o solo
        # se cayó la nube.
        lan_alive = self._probe_lan()
        titulo, cuerpo = _mensaje_offline(
            state, self._offline_first_seen_at,
            self._offline_notify_count, lan_alive,
        )
        log.warning("ALARMA OFFLINE #%d tras %d ticks (lan_alive=%s)",
                    self._offline_notify_count,
                    self._consecutive_offline, lan_alive)
        self.notifier.send(titulo, cuerpo)
        if primera:
            self._registrar("offline", titulo, cuerpo, now)
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
            self._registrar("reconectado", titulo, cuerpo, now)
        self._consecutive_offline = 0
        self._offline_first_seen_at = None
        self._offline_last_notified_at = None
        self._offline_notify_count = 0

    def tick(self) -> BAWState:
        """Una iteración del loop. Expuesto para tests."""
        state = self.fetch_fn()
        now = state.fetched_at.timestamp()
        self.last_state = state
        if state.online:
            self.last_online_state = state

        if not state.online:
            self._handle_offline(state, now)
            return state

        self._handle_recovered_from_offline(state, now)

        nuevas, recuperadas = self.tracker.update(state.fault_bitmap, now)

        for ev in nuevas:
            titulo, cuerpo = _mensaje_alarma(state, ev)
            log.warning("ALARMA bit=%d: %s", ev.bit, ev.name)
            self.notifier.send(titulo, cuerpo)
            # Solo la primera notificación de cada fault va al historial;
            # los recordatorios no, para no inflarlo.
            if ev.notify_count == 1:
                self._registrar("alarma", titulo, cuerpo, now)
        for ev in recuperadas:
            titulo, cuerpo = _mensaje_recuperado(state, ev)
            log.info("RECUPERADA bit=%d: %s", ev.bit, ev.name)
            self.notifier.send(titulo, cuerpo)
            self._registrar("recuperado", titulo, cuerpo, now)

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
