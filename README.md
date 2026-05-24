# baw-watcher

Daemon que vigila el monitor trifásico **BAW SmartWiFi 80A** del negocio
24/7 y avisa por Telegram (y opcionalmente WhatsApp) cuando hay un
problema eléctrico o el equipo se desconecta.

Corre como servicio `systemd` en **peppygate** (Debian). Es independiente
de peppysoft: alerta aunque la PC del negocio esté apagada.

## Qué hace

- Polea el BAW **por la red local** (protocolo Tuya v3.5 con tinytuya)
  cada pocos segundos. peppygate está en la misma red del negocio, así
  que llega directo al BAW sin pasar por la nube de Tuya ni depender
  de internet.
- Alerta ante **faults críticos** (corte, sobre/subtensión, fuga,
  sobrecorriente, sobrecalentamiento, etc.).
- Detecta cuando el BAW **deja de responder en la red local** —
  coherente con un corte de luz, BAW desenchufado, o caída del WiFi
  del negocio. Lo ubica en la LAN por su MAC, así que aguanta los
  cambios de IP por DHCP.
- También avisa si peppygate se queda sin internet (no afecta al
  monitoreo del BAW, pero bloquea la entrega de alertas).
- Guarda un **historial de eventos** y responde comandos por Telegram
  (`/estado`, `/historial`).

## Por qué LAN y no nube

El watcher arrancó cloud-only en mayo 2026. Aguantó hasta que el
**Trial Edition** del proyecto Tuya Cloud agotó cuota y la nube empezó
a contestar `'Please upgrade to the official version: Your quota of
Trial Edition is used up.'` Migrar a LAN sacó la dependencia de Tuya
Cloud, de su cuota, y del DNS/internet de peppygate para vigilar al
BAW. Único costo: las lecturas de fase V/A/W (DPs 6/7/8) no se
publican por LAN — son los únicos datos que perdimos. Los faults
críticos (DP 9), la energía acumulada, la temperatura, el relé y la
fuga sí están por LAN, que es todo lo que el watcher necesita para
alertar.

## Estructura

```
src/
├── baw_state.py    parser de DPs del BAW + catálogo de faults
├── tuya_lan.py     cliente tinytuya v3.5 contra el BAW por LAN
├── tuya_cloud.py   cliente Tuya Cloud (legacy, no usado por el watcher)
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

## Requisitos

- Python 3.11+
- `tinytuya>=1.18` (única dep externa; el resto es stdlib)

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
