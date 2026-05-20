"""
Tipos y parser compartidos con el módulo Red Eléctrica de peppysoft.

Mantenemos esto SEPARADO del checkout de peppysoft a propósito: el
watcher corre en peppygate (Debian, systemd), peppysoft corre en peppy
(Windows). Si los unificamos, el deploy se vuelve un fiasco. La logica
es chica y estable — duplicarla cuesta menos que un import path
cross-machine via Tailscale.

Si el firmware del BAW cambia los DPs, hay que actualizar AMBAS copias
(las dos viven en `git log`, fácil de hacer grep cross-repo).
"""
from __future__ import annotations

import base64
import logging
import struct
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

log = logging.getLogger(__name__)


# DP IDs confirmados contra Tuya `/v1.0/devices/{id}/specifications`.
DP_TOTAL_ENERGY_FORWARD = 1
DP_PHASE_A              = 6
DP_PHASE_B              = 7
DP_PHASE_C              = 8
DP_FAULT_FLAGS          = 9
DP_LEAKAGE_CURRENT_MA   = 15
DP_SWITCH               = 16
DP_TEMP_CURRENT_C       = 103
DP_REVERSE_ENERGY       = 110

# Mapeo de bits del DP `fault` (idéntico al `label` de la cloud spec).
FAULT_BITS = {
    0:  "Cortocircuito",
    1:  "Sobretensión transitoria",
    2:  "Sobrecarga",
    3:  "Fuga de corriente",
    4:  "Falla por temperatura",
    5:  "Alarma de incendio",
    6:  "Potencia excesiva",
    7:  "Falla en autotest",
    8:  "Sobrecorriente",
    9:  "Desbalance de corriente",
    10: "Sobretensión",
    11: "Subtensión",
    12: "Falta de fase",
    13: "Corte de red",
    14: "Interferencia magnética",
    15: "Saldo agotado (prepago)",
    16: "Sin saldo (prepago)",
    17: "Secuencia de fases incorrecta",
    18: "Desbalance de tensión",
    19: "Corriente muy baja",
}

# Bits que disparan alerta crítica (llamada telefónica / push fuerte).
# Decidido por el dueño: corte, sobre/subtensión, fuga, sobrecorriente,
# sobrecalentamiento/incendio.
CRITICAL_FAULT_BITS = frozenset({
    0,   # Cortocircuito
    2,   # Sobrecarga
    3,   # Fuga de corriente
    4,   # Falla por temperatura
    5,   # Alarma de incendio
    8,   # Sobrecorriente
    10,  # Sobretensión
    11,  # Subtensión
    12,  # Falta de fase
    13,  # Corte de red eléctrica
})


@dataclass
class Phase:
    voltage: float = 0.0
    current: float = 0.0
    power: float = 0.0


@dataclass
class BAWState:
    online: bool = False
    relay_on: bool = False
    phase_a: Phase = field(default_factory=Phase)
    phase_b: Phase = field(default_factory=Phase)
    phase_c: Phase = field(default_factory=Phase)
    total_power: float = 0.0
    total_energy_kwh: float = 0.0
    reverse_energy_kwh: float = 0.0
    temp_c: float = 0.0
    leakage_ma: float = 0.0
    fault_bitmap: int = 0
    faults: list[str] = field(default_factory=list)
    raw_dps: dict[str, Any] = field(default_factory=dict)
    fetched_at: datetime = field(default_factory=datetime.now)
    error: str | None = None

    @property
    def critical_fault_bits(self) -> set[int]:
        """Subconjunto de bits encendidos que están en CRITICAL_FAULT_BITS."""
        return {b for b in CRITICAL_FAULT_BITS if self.fault_bitmap & (1 << b)}

    @property
    def critical_faults(self) -> list[str]:
        return [FAULT_BITS[b] for b in sorted(self.critical_fault_bits)]


def _parse_phase(value: Any) -> Phase:
    if value is None:
        return Phase()
    try:
        if isinstance(value, (bytes, bytearray)):
            data = bytes(value)
        elif isinstance(value, str):
            if all(c in "0123456789abcdefABCDEF" for c in value) and len(value) >= 16:
                data = bytes.fromhex(value)
            else:
                data = base64.b64decode(value, validate=False)
        else:
            return Phase()
        if len(data) < 8:
            return Phase()
        v = struct.unpack(">H", data[0:2])[0] / 10.0
        i = int.from_bytes(data[2:5], "big") / 1000.0
        p = int.from_bytes(data[5:8], "big")
        return Phase(voltage=v, current=i, power=float(p))
    except Exception as exc:
        log.debug("parse phase failed: %s (value=%r)", exc, value)
        return Phase()


def parse_state(dps: dict[str, Any]) -> BAWState:
    """Convierte un dict de DPs (string keys, como devuelve tinytuya o
    el endpoint cloud renombrado) a un BAWState normalizado."""
    s = BAWState(online=True, raw_dps=dict(dps))

    def _g(dp):
        return dps.get(str(dp))

    s.relay_on = bool(_g(DP_SWITCH))
    s.phase_a = _parse_phase(_g(DP_PHASE_A))
    s.phase_b = _parse_phase(_g(DP_PHASE_B))
    s.phase_c = _parse_phase(_g(DP_PHASE_C))
    s.total_power = s.phase_a.power + s.phase_b.power + s.phase_c.power

    if (e := _g(DP_TOTAL_ENERGY_FORWARD)) is not None:
        try: s.total_energy_kwh = float(e) / 100.0
        except (TypeError, ValueError): pass

    if (r := _g(DP_REVERSE_ENERGY)) is not None:
        try: s.reverse_energy_kwh = float(r) / 100.0
        except (TypeError, ValueError): pass

    if (t := _g(DP_TEMP_CURRENT_C)) is not None:
        try: s.temp_c = float(t)
        except (TypeError, ValueError): pass

    if (lk := _g(DP_LEAKAGE_CURRENT_MA)) is not None:
        try: s.leakage_ma = float(lk)
        except (TypeError, ValueError): pass

    if (mask := _g(DP_FAULT_FLAGS)) is not None:
        try:
            bits = int(mask)
            s.fault_bitmap = bits
            s.faults = [name for bit, name in FAULT_BITS.items()
                        if bits & (1 << bit)]
        except (TypeError, ValueError): pass

    return s
