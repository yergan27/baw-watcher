"""
Cliente Tuya Cloud minimalista para el watcher.

El watcher NO usa tinytuya / protocolo LAN — el firmware del BAW no
publica los DPs de fase por LAN, así que el cliente híbrido que usa
peppysoft no aporta nada acá: la mitad de los datos saldrían igual de
la nube. Hacemos cloud-only para que el daemon tenga 1 sola dep y 1
sola superficie de fallas.

Latencia esperada: 500-1500 ms por fetch. Para alertas críticas con
polling cada 5s, es de sobra.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

from baw_state import BAWState, parse_state

log = logging.getLogger(__name__)


# Códigos cloud → DP IDs locales, para reusar `parse_state`.
_CLOUD_CODE_TO_DP = {
    "total_forward_energy": "1",
    "phase_a": "6",
    "phase_b": "7",
    "phase_c": "8",
    "fault": "9",
    "leakage_current": "15",
    "switch": "16",
    "temp_current": "103",
    "reverse_energy_total": "110",
}


class TuyaCloudClient:
    def __init__(self, client_id: str, client_secret: str, device_id: str,
                 endpoint: str = "https://openapi.tuyaus.com"):
        if not (client_id and client_secret and device_id):
            raise ValueError("client_id, client_secret y device_id son requeridos")
        self.client_id = client_id
        self.client_secret = client_secret
        self.device_id = device_id
        self.endpoint = endpoint.rstrip("/")
        self._token: str | None = None
        self._token_exp: float = 0.0

    def _sign(self, method: str, path: str, body: str,
              access_token: str) -> tuple[str, str, str]:
        t = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        content_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
        string_to_sign = f"{method}\n{content_sha}\n\n{path}"
        sign_str = self.client_id + access_token + t + nonce + string_to_sign
        sign = hmac.new(
            self.client_secret.encode("utf-8"),
            sign_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest().upper()
        return t, nonce, sign

    def _request(self, method: str, path: str,
                 require_auth: bool = True) -> dict:
        body = ""
        token = self._access_token() if require_auth else ""
        t, nonce, sign = self._sign(method, path, body, token)
        headers = {
            "client_id": self.client_id,
            "sign": sign,
            "sign_method": "HMAC-SHA256",
            "t": t,
            "nonce": nonce,
            "Content-Type": "application/json",
        }
        if token:
            headers["access_token"] = token
        req = urlrequest.Request(
            self.endpoint + path, headers=headers, method=method,
        )
        with urlrequest.urlopen(req, timeout=10) as r:
            return json.load(r)

    def _access_token(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        resp = self._request("GET", "/v1.0/token?grant_type=1",
                              require_auth=False)
        if not resp.get("success"):
            raise RuntimeError(f"token fail: {resp}")
        r = resp["result"]
        self._token = r["access_token"]
        self._token_exp = time.time() + r["expire_time"]
        log.info("Tuya cloud token refreshed (expires_in=%ds)",
                 r["expire_time"])
        return self._token

    def fetch_state(self) -> BAWState:
        # Usamos /v1.0/devices/{id} en vez de /status: devuelve BOTH el
        # estado del dispositivo (campo `online`) y los DPs (`status`)
        # en una sola request. Sin esto, la cloud responde success=true
        # con los últimos valores aunque el BAW haya perdido alimentación
        # hace horas — y nunca nos enteraríamos del corte de luz.
        try:
            resp = self._request(
                "GET", f"/v1.0/devices/{self.device_id}",
            )
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            return BAWState(online=False, error=f"cloud: {exc}")
        except Exception as exc:
            log.exception("cloud unexpected error")
            return BAWState(online=False, error=f"cloud: {exc}")
        if not resp.get("success"):
            return BAWState(online=False, error=f"cloud: {resp}")
        result = resp.get("result") or {}
        if not result.get("online", False):
            return BAWState(
                online=False,
                error="device.online=false (BAW desconectado de la nube)",
            )
        # status: list[{code, value}]. Convertimos a DP-keyed dict y
        # reusamos `parse_state` (mismo parser que peppysoft).
        dps = {
            _CLOUD_CODE_TO_DP[item["code"]]: item.get("value")
            for item in result.get("status", [])
            if item.get("code") in _CLOUD_CODE_TO_DP
        }
        return parse_state(dps)
