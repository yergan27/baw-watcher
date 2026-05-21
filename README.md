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
- Detecta cuando el BAW **deja de reportar** y distingue de quién es
  el problema:
  - *corte de luz* — el BAW no responde ni por la nube ni por la red
    local (lo ubica en la LAN por su MAC, aunque DHCP le haya cambiado
    la IP);
  - *caída de la nube del BAW* — el BAW sí responde local (tiene luz y
    WiFi) pero perdió la nube de Tuya;
  - *caída de la conexión de peppygate* — es peppygate el que se quedó
    sin internet o sin DNS. No es un problema del BAW ni eléctrico; se
    avisa como tal, y solo si la caída dura más que un parpadeo.
- Guarda un **historial de eventos** y responde comandos por Telegram
  (`/estado`, `/historial`).

## Estructura

```
src/
├── baw_state.py    parser de DPs del BAW + catálogo de faults
├── tuya_cloud.py   cliente Tuya Cloud (firma HMAC, stdlib)
├── lan_probe.py    ubica al BAW en la LAN por su MAC (resiste DHCP)
├── net_probe.py    chequea si peppygate tiene internet (por IP, sin DNS)
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
