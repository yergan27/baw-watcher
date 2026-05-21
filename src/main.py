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

from commands import TelegramCommandBot
from history import HistoryStore
from lan_probe import BAWLocator, baw_responde_en_lan
from net_probe import hay_internet
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

    # ── Historial de eventos ───────────────────────────────────────
    # systemd con `StateDirectory=baw-watcher` deja la DB en
    # /var/lib/baw-watcher y exporta STATE_DIRECTORY. Fuera de systemd
    # (correr a mano, tests) cae al directorio actual.
    history_path = _env("HISTORY_DB_PATH") or os.path.join(
        os.environ.get("STATE_DIRECTORY", "."), "history.db")
    history = HistoryStore(history_path)

    # ── Chequeo del BAW en la red local ────────────────────────────
    # Con esto, las alertas de desconexión distinguen "corte de luz" de
    # "se cayó la nube". Preferimos identificar al BAW por su MAC (fija)
    # porque el router le cambia la IP por DHCP. BAW_LAN_IP queda como
    # pista inicial opcional. Sin MAC ni IP, el watcher sigue
    # funcionando con el diagnóstico genérico.
    lan_probe_fn = None
    baw_lan_mac = _env("BAW_LAN_MAC")
    baw_lan_ip = _env("BAW_LAN_IP")
    if baw_lan_mac:
        locator = BAWLocator(mac=baw_lan_mac, ip_conocida=baw_lan_ip or None)
        lan_probe_fn = locator.esta_vivo
        log.info("Chequeo LAN habilitado — BAW por MAC %s", baw_lan_mac)
    elif baw_lan_ip:
        lan_probe_fn = lambda: baw_responde_en_lan(baw_lan_ip)  # noqa: E731
        log.info("Chequeo LAN habilitado — BAW por IP fija %s", baw_lan_ip)
    else:
        log.warning("Sin BAW_LAN_MAC ni BAW_LAN_IP — las alertas de "
                    "desconexión no podrán distinguir corte de luz de "
                    "caída de la nube")

    # ── Chequeo de internet de peppygate ──────────────────────────
    # Distingue "el BAW perdió la nube" de "peppygate se quedó sin
    # conexión". Si es lo segundo, no es un problema del BAW ni de la
    # luz — y no hay que alarmar al dueño como si lo fuera. Es un
    # chequeo por IP fija (sin DNS), siempre disponible — no necesita
    # configuración.
    log.info("Chequeo de internet habilitado (IPs públicas, sin DNS)")

    # ── Watcher ───────────────────────────────────────────────────
    poll_s = float(_env("POLL_INTERVAL_S", "5"))
    repeat_s = float(_env("REPEAT_AFTER_S", str(30 * 60)))
    offline_after = int(_env("OFFLINE_ALERT_AFTER_TICKS", "3"))
    internet_after_s = float(_env("INTERNET_ALERT_AFTER_S", "120"))
    watcher = Watcher(
        fetch_fn=client.fetch_state,
        notifier=notifier,
        poll_interval_s=poll_s,
        repeat_after_s=repeat_s,
        offline_alert_after_ticks=offline_after,
        lan_probe_fn=lan_probe_fn,
        internet_probe_fn=hay_internet,
        internet_alert_after_s=internet_after_s,
        history=history,
    )

    # ── Bot de comandos de Telegram ────────────────────────────────
    # Camino de entrada: el operador pregunta /estado o /historial.
    # Solo se habilita si Telegram está configurado.
    if tg_token and tg_chats:
        bot = TelegramCommandBot(
            token=tg_token,
            allowed_chat_ids=tg_chats,
            handlers={
                "estado": watcher.estado_texto,
                "historial": history.resumen_texto,
            },
        )
        bot.start()
        log.info("Bot de comandos habilitado (/estado, /historial)")

    watcher.run()


if __name__ == "__main__":
    main()
