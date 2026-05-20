"""
Historial de eventos del baw-watcher en SQLite.

Guarda cada alerta y cada recuperación para poder responder el comando
`/historial` por Telegram. La DB vive en el StateDirectory del servicio
(`/var/lib/baw-watcher/history.db`) — sobrevive a reinicios del daemon.

Es mucho más escritura puntual que lectura; SQLite de la stdlib alcanza
de sobra. Cada operación abre su propia conexión: así el thread del
watcher (que escribe) y el del bot de comandos (que lee) no comparten
una conexión — sqlite3 no es thread-safe entre threads sobre la misma
conexión.

Si la DB no se puede abrir (disco lleno, permisos), el watcher NO debe
caerse: `HistoryStore` loggea el problema y queda en modo no-op.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime

log = logging.getLogger(__name__)


class HistoryStore:
    """Registro append-only de eventos. `path` es la ruta del archivo
    SQLite. Si no se puede inicializar, queda deshabilitado (no-op)."""

    def __init__(self, path: str):
        self.path = path
        self._disabled = False
        try:
            self._init_schema()
            log.info("history: usando %s", path)
        except sqlite3.Error as exc:
            log.error("history: no se pudo abrir %s — historial "
                      "deshabilitado: %s", path, exc)
            self._disabled = True

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS eventos (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts      REAL NOT NULL,
                    kind    TEXT NOT NULL,
                    titulo  TEXT NOT NULL,
                    detalle TEXT NOT NULL DEFAULT ''
                )
            """)

    def record(self, kind: str, titulo: str, detalle: str = "",
               ts: float | None = None) -> None:
        """Registra un evento. `kind` es una etiqueta corta
        ('offline', 'reconectado', 'alarma', 'recuperado'). Nunca
        propaga errores — un fallo de DB no debe tumbar el watcher."""
        if self._disabled:
            return
        if ts is None:
            ts = time.time()
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO eventos (ts, kind, titulo, detalle) "
                    "VALUES (?,?,?,?)", (ts, kind, titulo, detalle))
        except sqlite3.Error as exc:
            log.warning("history: no se pudo guardar '%s': %s", titulo, exc)

    def recent(self, limit: int = 15) -> list[dict]:
        """Últimos `limit` eventos, del más nuevo al más viejo."""
        if self._disabled:
            return []
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT ts, kind, titulo, detalle FROM eventos "
                    "ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error as exc:
            log.warning("history: no se pudo leer el historial: %s", exc)
            return []

    def resumen_texto(self, limit: int = 15) -> str:
        """Historial formateado para mandar como respuesta de Telegram."""
        eventos = self.recent(limit)
        if not eventos:
            return ("Todavía no hay eventos registrados.\n"
                    "Cuando pase algo (corte, desconexión, fault) va a "
                    "quedar acá.")
        lineas = [f"📋 Últimos {len(eventos)} evento(s):", ""]
        for ev in eventos:
            hora = datetime.fromtimestamp(ev["ts"]).strftime("%d/%m %H:%M")
            lineas.append(f"• {hora} — {ev['titulo']}")
        return "\n".join(lineas)
