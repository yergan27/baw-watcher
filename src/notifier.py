"""
Notificadores multi-canal. Cada canal implementa `send(titulo, cuerpo)`
y devuelve True si llegó, False si falló. El watcher itera todos los
canales habilitados — si uno cae, los demás siguen entregando.

Canales implementados:
  - TelegramNotifier  → bot API, gratis, push agresivo en cada cel
    suscripto al bot
  - WhatsAppNotifier  → Meta Cloud API, usa el WA Business ya
    configurado en peppysoft

Convención: cada canal es independiente y silencioso ante fallas (log
warning, devuelve False) — NO propagar excepciones al watcher porque
un canal roto no debe bloquear los demás.
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

log = logging.getLogger(__name__)


class Notifier(ABC):
    """Interfaz común: cada canal sabe enviar (título, cuerpo) → bool."""

    name: str

    @abstractmethod
    def send(self, titulo: str, cuerpo: str) -> bool:
        ...


# ── Telegram ──────────────────────────────────────────────────────────

class TelegramNotifier(Notifier):
    """Envía un mensaje a cada chat_id de la lista usando el Bot API.

    `disable_notification=False` y un texto con MarkdownV2 garantiza que
    aparezca con sonido. Para que SUENE como alarma incluso con el cel
    en silencio, cada usuario tiene que configurar el chat del bot con
    "notificación importante" en su Telegram (Settings → Notifications
    & Sounds → Custom → para este chat → sonido especial).
    """

    name = "telegram"
    API = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, token: str, chat_ids: list[int | str]):
        if not token or not chat_ids:
            raise ValueError("token y chat_ids son requeridos")
        self.token = token
        self.chat_ids = list(chat_ids)

    def send(self, titulo: str, cuerpo: str) -> bool:
        url = self.API.format(token=self.token)
        # Telegram NO interpreta MarkdownV2 si no escapamos los caracteres
        # especiales — para evitar el quilombo de escape, mandamos sin
        # parse_mode (texto plano).
        text = f"🚨 *{titulo}*\n\n{cuerpo}".replace("*", "")
        any_ok = False
        for chat_id in self.chat_ids:
            payload = {
                "chat_id": chat_id,
                "text": text,
                "disable_notification": False,
            }
            try:
                req = urlrequest.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlrequest.urlopen(req, timeout=10) as r:
                    resp = json.load(r)
                if not resp.get("ok"):
                    log.warning("telegram chat_id=%s NO ok: %s",
                                chat_id, resp)
                    continue
                any_ok = True
            except (HTTPError, URLError, TimeoutError, OSError) as exc:
                log.warning("telegram chat_id=%s falló: %s", chat_id, exc)
            except Exception as exc:
                log.exception("telegram chat_id=%s error inesperado: %s",
                              chat_id, exc)
        return any_ok


# ── WhatsApp (Meta Cloud API) ─────────────────────────────────────────

class WhatsAppNotifier(Notifier):
    """Manda un mensaje de WhatsApp via Meta Cloud API a una lista de
    números (formato internacional sin +, ej. 5491159753648).

    Reusa el `WA_TOKEN` y `WA_PHONE_ID` que peppysoft ya tiene
    configurados para los pedidos. Si los 24h windows estuvieran
    vencidos para el destinatario, se necesita un template aprobado —
    para alertas raras (esperamos < 1000/mes) Meta tiene free tier
    suficiente.
    """

    name = "whatsapp"
    API = "https://graph.facebook.com/v20.0/{phone_id}/messages"

    def __init__(self, token: str, phone_id: str, to_numbers: list[str]):
        if not (token and phone_id and to_numbers):
            raise ValueError("token, phone_id y to_numbers son requeridos")
        self.token = token
        self.phone_id = phone_id
        self.to_numbers = list(to_numbers)

    def send(self, titulo: str, cuerpo: str) -> bool:
        url = self.API.format(phone_id=self.phone_id)
        text = f"🚨 {titulo}\n\n{cuerpo}"
        any_ok = False
        for number in self.to_numbers:
            payload = {
                "messaging_product": "whatsapp",
                "to": number,
                "type": "text",
                "text": {"body": text},
            }
            try:
                req = urlrequest.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with urlrequest.urlopen(req, timeout=10) as r:
                    resp = json.load(r)
                if "messages" not in resp:
                    # Meta devuelve error con shape distinto si falla.
                    log.warning("whatsapp %s NO ok: %s", number, resp)
                    continue
                any_ok = True
            except HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                log.warning("whatsapp %s HTTP %s: %s",
                            number, exc.code, body[:200])
            except (URLError, TimeoutError, OSError) as exc:
                log.warning("whatsapp %s falló: %s", number, exc)
            except Exception as exc:
                log.exception("whatsapp %s error inesperado: %s",
                              number, exc)
        return any_ok


# ── Multi-canal ──────────────────────────────────────────────────────

class MultiNotifier(Notifier):
    """Itera todos los canales habilitados. Devuelve True si AL MENOS
    uno entregó — esa es la garantía: que al menos un cel suene."""

    name = "multi"

    def __init__(self, channels: list[Notifier]):
        self.channels = [c for c in channels if c is not None]

    def send(self, titulo: str, cuerpo: str) -> bool:
        if not self.channels:
            log.warning("MultiNotifier sin canales — alerta perdida")
            return False
        results = []
        for ch in self.channels:
            try:
                ok = ch.send(titulo, cuerpo)
            except Exception as exc:
                log.exception("canal %s lanzó excepción: %s", ch.name, exc)
                ok = False
            results.append((ch.name, ok))
        log.info("alerta '%s' entregada via: %s",
                 titulo,
                 ", ".join(f"{n}={'OK' if ok else 'FAIL'}"
                            for n, ok in results))
        return any(ok for _, ok in results)
