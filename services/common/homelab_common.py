"""Utilidades compartidas por netwatch y el bot (solo biblioteca estándar + PyYAML)."""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("homelab")


def setup_logging(name: str) -> logging.Logger:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return logging.getLogger(name)


def load_config() -> dict:
    path = Path(os.environ.get("HOMELAB_CONFIG", "/config/homelab.yml"))
    if not path.exists():
        alt = Path(__file__).resolve().parents[2] / "config" / "homelab.example.yml"
        log.warning("No existe %s; uso %s", path, alt)
        path = alt
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def data_dir() -> Path:
    d = Path(os.environ.get("DATA_DIR", "/data"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def http_json(url: str, method: str = "GET", body: Any = None, headers: dict | None = None,
              timeout: float = 15) -> Any:
    """Petición HTTP con JSON. Lanza RuntimeError con un mensaje legible si falla."""
    data = None
    hdrs = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} en {url.split('?')[0]}: {e.read()[:300]!r}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"No se pudo conectar a {url.split('?')[0]}: {e}") from e
    return json.loads(raw) if raw else None


def prom_query(base: str, expr: str, at: float | None = None) -> list[dict]:
    q = {"query": expr}
    if at:
        q["time"] = str(at)
    res = http_json(f"{base.rstrip('/')}/api/v1/query?{urllib.parse.urlencode(q)}")
    return res.get("data", {}).get("result", [])


def prom_range(base: str, expr: str, seconds: int, step: int) -> list[dict]:
    end = time.time()
    q = {"query": expr, "start": str(end - seconds), "end": str(end), "step": str(step)}
    res = http_json(f"{base.rstrip('/')}/api/v1/query_range?{urllib.parse.urlencode(q)}",
                    timeout=30)
    return res.get("data", {}).get("result", [])


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def human_bps(n: float) -> str:
    for unit in ("bps", "Kbps", "Mbps", "Gbps"):
        if abs(n) < 1000 or unit == "Gbps":
            return f"{n:.0f} {unit}" if unit == "bps" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} Gbps"
