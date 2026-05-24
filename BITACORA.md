# Bitácora — baw-watcher

Registro cronológico de incidentes, cambios y decisiones del sistema de
monitoreo del BAW. La entrada más reciente va arriba.

Esto complementa el historial de git y los PRs (ahí está el detalle
técnico fino); acá queda el "qué pasó y por qué" en lenguaje claro, para
poder repasarlo sin leer código.

---

## 2026-05-24 — La nube de Tuya agotó su Trial → migración a LAN

**Síntoma:** lluvia de alertas por Telegram tipo "⚠️ BAW sin conexión a
la nube (recordatorio #30, #31, ...)". El watcher SÍ reconocía que el
BAW estaba vivo en la red local — el mensaje aclaraba "El BAW SÍ
responde en la red local: tiene luz y está conectado al WiFi" — pero el
recordatorio cada 30 min era ruido constante.

**Causa real:** el detalle técnico de las alertas mostraba
`'code': 28841004, 'msg': 'Please upgrade to the official version: Your
quota of Trial Edition is used up.'`. El proyecto cloud "Peppysoft BAW"
en `iot.tuya.com` se creó como Trial Edition y se le agotó la cuota a
los pocos días — Tuya bloquea el endpoint hasta que se "extiende" el
trial o se paga. El BAW no tenía ningún problema; la nube de Tuya
simplemente nos dejó afuera.

**Arreglo:** se migró el watcher de **cloud-only a LAN-only**. Antes el
único path de lectura era `tuya_cloud.py` (HTTP firmado contra
`openapi.tuyaus.com`). Ahora el watcher usa `tuya_lan.py` — cliente
tinytuya v3.5 que habla directo al BAW por el puerto 6668 de la red del
negocio. peppygate ya estaba en esa red (por las cámaras), así que no
hizo falta nada extra a nivel de red. El locator por MAC ya existía
para los chequeos de "BAW vivo en LAN" — se reusa para encontrar la IP
actual del BAW aunque DHCP la rote.

**Lo que se pierde:** las lecturas instantáneas de fase V/A/W (DPs 6, 7
y 8). El firmware del BAW no publica esos DPs por LAN — solo por la
nube. Los mensajes de alarma omitieron esas líneas cuando no están
disponibles; queda el nombre del fault, la energía acumulada y la
temperatura, que es lo importante.

**Lo que se gana:** independencia total de la nube de Tuya, su cuota y
su DNS/internet. El watcher solo necesita estar en la misma LAN que el
BAW. Mientras peppygate y el BAW estén vivos en la red del negocio, las
alertas funcionan — internet de peppygate solo hace falta para entregar
las notificaciones de Telegram.

**Estado:** migración mergeada, deployada en peppygate, servicio
reiniciado y sano. 79 tests verdes (71 previos + 8 nuevos para el
cliente LAN). El proyecto cloud queda olvidable; si en el futuro hace
falta cloud (p.ej. para histórico mensual de kWh), peppysoft tiene su
propio cliente cloud y los tokens en `gestion_app/.env`.

---

## 2026-05-20 — Falsas alertas de "BAW sin conexión a la nube"

**Síntoma:** el dueño recibía por Telegram alertas de "BAW sin conexión a
la nube" que se reconectaban solas a los ~6 minutos.

**Causa real:** no era el BAW. Era **peppygate quedándose sin DNS** (su
forma de traducir nombres de internet). peppygate dependía del router del
negocio para el DNS, y ese router parpadea. El BAW estuvo funcionando
bien todo el tiempo. Encima, durante esas caídas Telegram tampoco se
podía contactar, así que la alerta ni llegaba — solo el "reconectado".

**Arreglos:**

1. *Watcher (PR #3):* ahora distingue tres causas cuando el BAW deja de
   reportar — corte de luz, caída de la nube del BAW, y caída de la
   conexión de peppygate. Si es lo último, avisa "📶 Sin conexión en
   peppygate" aclarando que NO es un problema del BAW ni eléctrico, y
   solo si la caída dura más de 2 minutos (los parpadeos cortos ya no
   molestan). Se agregó `net_probe.py`, que chequea internet por IP fija
   (sin DNS).
2. *DNS de raíz:* se configuraron servidores DNS confiables (Cloudflare
   1.1.1.1 y Google 8.8.8.8) en la consola de Tailscale, con "Override
   local DNS" activado. peppygate ya no depende del router parpadeante.

**Estado:** PR #3 mergeado y desplegado en peppygate (servicio reiniciado
y sano). DNS de Tailscale verificado (`tailscale dns status`). Alerta de
prueba por Telegram enviada y confirmada por el dueño.

**Pendiente:** sumar el chat de Telegram del papá del dueño al canal de
alertas — hoy las alertas solo llegan al celular del dueño.
