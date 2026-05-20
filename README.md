# baw-watcher

Daemon que vigila el monitor trifásico **BAW SmartWiFi 80A** del negocio
24/7 y avisa por Telegram (y opcionalmente WhatsApp) cuando hay un
problema eléctrico o el equipo se desconecta.

Corre como servicio `systemd` en **peppygate** (Debian). Es independiente
de peppysoft: alerta aunque la PC del negocio esté apagada.

## Qué hace

- Polea el BAW por la **nube de Tuya** cada pocos segundos.
- Alerta ante **faults críticos** (corte, sobre/subtensión, fuga,
  sobrecorriente, sobrecalentamiento, etc.).
- Detecta cuando el BAW **deja de reportar**. Si hay IP local
  configurada, distingue entre *corte de luz* (el BAW tampoco responde
  en la red local) y *caída de la conexión a la nube* (el BAW sí
  responde local — tiene luz y WiFi).
- Guarda un **historial de eventos** y responde comandos por Telegram
  (`/estado`, `/historial`).

## Estructura

```
src/
├── baw_state.py    parser de DPs del BAW + catálogo de faults
├── tuya_cloud.py   cliente Tuya Cloud (firma HMAC, stdlib)
├── lan_probe.py    chequeo TCP del BAW en la red local
├── notifier.py     canales de alerta (Telegram, WhatsApp, multi)
├── history.py      persistencia de eventos en SQLite
├── commands.py     bot de Telegram que responde /estado y /historial
├── watcher.py      loop principal + detección con debounce
└── main.py         entry point — lee config del entorno
tests/              suite con pytest
baw-watcher.service unit de systemd
install.sh          instalador para peppygate
```

## Instalación / deploy (en peppygate)

```bash
sudo ./install.sh
sudo nano /etc/baw-watcher/baw-watcher.env   # completar secretos
sudo systemctl restart baw-watcher
sudo journalctl -u baw-watcher -f            # ver logs
```

## Tests

```bash
python -m pytest
```
