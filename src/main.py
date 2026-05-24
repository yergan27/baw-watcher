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
from lan_probe import BAWLocator
from net_probe import hay_internet
from notifier import MultiNotifier, TelegramNotifier, WhatsAppNotifier
from tuya_lan import TuyaLanClient
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

    # ── BAW por LAN (única fuente de datos) ────────────────────────
    # Cloud-only era inviable: el Trial Edition del proyecto Tuya se
    # agota y el endpoint empieza a devolver "Please upgrade to the
    # official version". Por LAN, peppygate llega directo al BAW
    # (misma red del negocio) y no depende de internet ni de cuotas.
    baw_mac = _env("BAW_LAN_MAC", required=True)
    baw_ip_hint = _env("BAW_LAN_IP") or None
    locator = BAWLocator(mac=baw_mac, ip_conocida=baw_ip_hint)
    client = TuyaLanClient(
        device_id=_env("TUYA_BAW_DEVICE_ID", required=True),
        local_key=_env("TUYA_BAW_LOCAL_KEY", required=True),
        locator=locator,
    )
    log.info("Cliente BAW por LAN — MAC %s (IP inicial: %s)",
             baw_mac, baw_ip_hint or "auto")

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
    # Reusa el mismo locator del cliente — si el fetch falla, este
    # probe va a fallar igual (el BAW no está respondiendo a nada por
    # LAN), y el mensaje queda como "posible corte de luz". Cuando el
    # fetch funciona, está implícito que el BAW está vivo en la LAN.
    lan_probe_fn = locator.esta_vivo

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
