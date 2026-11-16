"""python -m netwatch  → escaneo en segundo plano + web/API/métricas."""
from __future__ import annotations

import os
import threading

import uvicorn

from homelab_common import data_dir, load_config, setup_logging

from .db import DB
from .monitor import Monitor
from .web import build_app


def main() -> None:
    log = setup_logging("netwatch")
    cfg = load_config()
    data = data_dir()
    db = DB(data / "netwatch.db")
    mon = Monitor(cfg, db, data)
    if os.environ.get("NETWATCH_NO_SCAN") != "1":
        threading.Thread(target=mon.run_forever, name="monitor", daemon=True).start()
    host, _, port = os.environ.get("LISTEN", "0.0.0.0:8000").rpartition(":")
    log.info("Web en http://%s:%s", host, port)
    uvicorn.run(build_app(mon), host=host, port=int(port), log_level="warning",
                proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
