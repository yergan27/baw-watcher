"""
Entry point del daemon baw-watcher.

Lee la configuración del entorno (cargado por systemd vía EnvironmentFile
o desde /etc/baw-watcher/baw-watcher.env) y arranca el loop. Si falta
algún secreto crítico, abortamos al toque con un error claro en
journalctl para que el operador lo vea con `systemctl status`.
"""
from __future__ import annotations

import logging
import os
import sys

from notifier import MultiNotifier, TelegramNotifier, WhatsAppNotifier
from tuya_cloud import TuyaCloudClient
from watcher import Watcher

log = logging.getLogger("baw-watcher")


def _env(name: str, default: str | None = None, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and not val:
        log.error("falta variable de entorno: %s", name)
        sys.exit(2)
    return val or ""


def _env_list(name: str) -> list[str]:
    raw = os.environ.get(name, "").strip()
    return [x.strip() for x in raw.split(",") if x.strip()]


def main():
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
    )

    # ── Tuya cloud (única fuente de datos) ─────────────────────────
    client = TuyaCloudClient(
        client_id=_env("TUYA_CLIENT_ID", required=True),
        client_secret=_env("TUYA_CLIENT_SECRET", required=True),
        device_id=_env("TUYA_BAW_DEVICE_ID", required=True),
        endpoint=_env("TUYA_ENDPOINT", "https://openapi.tuyaus.com"),
    )

    # ── Notificadores ──────────────────────────────────────────────
    channels = []

    tg_token = _env("TELEGRAM_BOT_TOKEN")
    tg_chats = _env_list("TELEGRAM_CHAT_IDS")
    if tg_token and tg_chats:
        channels.append(TelegramNotifier(token=tg_token, chat_ids=tg_chats))
        log.info("Telegram habilitado: %d chat(s)", len(tg_chats))
    else:
        log.warning("Telegram deshabilitado (faltan TELEGRAM_BOT_TOKEN / "
                    "TELEGRAM_CHAT_IDS)")

    wa_token = _env("WA_TOKEN")
    wa_phone_id = _env("WA_PHONE_ID")
    wa_numbers = _env_list("WA_ALERT_NUMBERS")
    if wa_token and wa_phone_id and wa_numbers:
        channels.append(WhatsAppNotifier(
            token=wa_token, phone_id=wa_phone_id, to_numbers=wa_numbers,
        ))
        log.info("WhatsApp habilitado: %d número(s)", len(wa_numbers))
    else:
        log.warning("WhatsApp deshabilitado (faltan WA_TOKEN / WA_PHONE_ID / "
                    "WA_ALERT_NUMBERS)")

    if not channels:
        log.error("ningún canal de notificación configurado — abortando")
        sys.exit(2)

    notifier = MultiNotifier(channels=channels)

    # ── Watcher ───────────────────────────────────────────────────
    poll_s = float(_env("POLL_INTERVAL_S", "5"))
    repeat_s = float(_env("REPEAT_AFTER_S", str(30 * 60)))
    offline_after = int(_env("OFFLINE_ALERT_AFTER_TICKS", "6"))
    Watcher(
        fetch_fn=client.fetch_state,
        notifier=notifier,
        poll_interval_s=poll_s,
        repeat_after_s=repeat_s,
        offline_alert_after_ticks=offline_after,
    ).run()


if __name__ == "__main__":
    main()
