"""
Bot de Telegram que responde comandos del baw-watcher.

El watcher manda alertas (camino de salida). Este módulo agrega el
camino de entrada: el operador le escribe un comando al bot y recibe
una respuesta al toque.

Comandos:
  /estado     → lectura actual del BAW (fases, online/offline)
  /historial  → últimos eventos registrados
  /start, /help y cualquier otro → ayuda

Usa long-polling sobre `getUpdates` (no webhook: peppygate no tiene
URL pública). Corre en su propio thread daemon; si la API de Telegram
falla, reintenta sin tumbar el watcher.

Seguridad: solo responde a los `chat_id` de la lista autorizada (los
mismos que reciben alertas). Un comando de un chat desconocido se
ignora — no exponemos el estado eléctrico del negocio a cualquiera.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Callable
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

log = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"


def _api_call(token: str, method: str, params: dict, timeout: float) -> dict:
    """POST a la Bot API. Devuelve el JSON parseado; propaga errores de
    red para que el caller decida cómo reintentar."""
    url = _API.format(token=token, method=method)
    req = urlrequest.Request(
        url,
        data=json.dumps(params).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=timeout) as r:
        return json.load(r)


class TelegramCommandBot(threading.Thread):
    """Thread daemon que escucha comandos y los responde.

    `handlers` mapea nombre de comando (sin la barra) → callable sin
    argumentos que devuelve el texto de respuesta. Los comandos no
    listados caen en la ayuda.
    """

    def __init__(self, token: str, allowed_chat_ids: list,
                 handlers: dict[str, Callable[[], str]],
                 long_poll_s: int = 25):
        super().__init__(name="telegram-cmd", daemon=True)
        if not token:
            raise ValueError("token requerido")
        self.token = token
        # Normalizamos a str: la API devuelve el chat id como int.
        self.allowed = {str(c) for c in allowed_chat_ids}
        self.handlers = dict(handlers)
        self.long_poll_s = long_poll_s
        self._stop = threading.Event()
        self._offset = 0

    def stop(self) -> None:
        self._stop.set()

    # ── Loop ─────────────────────────────────────────────────────────

    def run(self) -> None:
        log.info("telegram-cmd: arranca (comandos: %s)",
                 ", ".join(sorted(self.handlers)) or "ninguno")
        self._drain_pending()
        while not self._stop.is_set():
            try:
                self._poll_once()
            except (HTTPError, URLError, TimeoutError, OSError) as exc:
                log.warning("telegram-cmd: getUpdates falló: %s", exc)
                self._stop.wait(5)
            except Exception:
                log.exception("telegram-cmd: error inesperado")
                self._stop.wait(5)
        log.info("telegram-cmd: stop limpio")

    def _drain_pending(self) -> None:
        """Descarta comandos viejos acumulados antes de arrancar — no
        queremos responder algo que el operador mandó hace horas."""
        try:
            resp = _api_call(self.token, "getUpdates",
                             {"offset": -1, "timeout": 0}, timeout=15)
        except Exception as exc:
            log.warning("telegram-cmd: no pude drenar updates viejos: %s", exc)
            return
        for upd in resp.get("result", []):
            self._offset = max(self._offset, upd["update_id"] + 1)

    def _poll_once(self) -> None:
        resp = _api_call(self.token, "getUpdates", {
            "offset": self._offset,
            "timeout": self.long_poll_s,
            "allowed_updates": ["message"],
        }, timeout=self.long_poll_s + 10)
        if not resp.get("ok"):
            log.warning("telegram-cmd: getUpdates no ok: %s", resp)
            self._stop.wait(5)
            return
        for upd in resp.get("result", []):
            self._offset = max(self._offset, upd["update_id"] + 1)
            self._handle_update(upd)

    # ── Manejo de comandos ───────────────────────────────────────────

    def _handle_update(self, upd: dict) -> None:
        msg = upd.get("message") or {}
        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            return
        # "/comando" o "/comando@MiBot arg" → "comando"
        cmd = text[1:].split()[0].split("@")[0].lower()

        if chat_id not in self.allowed:
            log.warning("telegram-cmd: '/%s' de chat NO autorizado %s — "
                        "ignorado", cmd, chat_id)
            return

        handler = self.handlers.get(cmd)
        if handler is None:
            self._reply(chat_id, self._ayuda())
            return
        try:
            respuesta = handler()
        except Exception as exc:
            log.exception("telegram-cmd: handler '/%s' falló", cmd)
            respuesta = f"No pude responder /{cmd} ahora mismo: {exc}"
        self._reply(chat_id, respuesta)

    def _ayuda(self) -> str:
        cmds = "\n".join(f"/{c}" for c in sorted(self.handlers))
        return ("🤖 Bot del monitor eléctrico.\n\n"
                "Comandos disponibles:\n" + (cmds or "(ninguno)"))

    def _reply(self, chat_id: str, text: str) -> None:
        try:
            _api_call(self.token, "sendMessage", {
                "chat_id": chat_id,
                "text": text,
                "disable_notification": True,
            }, timeout=10)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            log.warning("telegram-cmd: no pude responder a %s: %s",
                        chat_id, exc)
