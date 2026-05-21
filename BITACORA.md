# Bitácora — baw-watcher

Registro cronológico de incidentes, cambios y decisiones del sistema de
monitoreo del BAW. La entrada más reciente va arriba.

Esto complementa el historial de git y los PRs (ahí está el detalle
técnico fino); acá queda el "qué pasó y por qué" en lenguaje claro, para
poder repasarlo sin leer código.

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
