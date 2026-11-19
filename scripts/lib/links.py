"""Botones del panel (homelab.yml → links), para los scripts.

  python3 scripts/lib/links.py add NOMBRE URL ICONO "DESCRIPCIÓN" [URL_PARA_REVISAR]
  python3 scripts/lib/links.py remove NOMBRE
Con group: Servicios aparecen en Inicio y en la pestaña Servicios con su punto verde/rojo.
"""
import sys
from pathlib import Path

import yaml

P = Path(__file__).resolve().parents[2] / "config" / "homelab.yml"


def main(argv: list[str]) -> int:
    if not P.exists():
        print("  (sin config/homelab.yml aquí: el botón se agrega en la máquina del panel)")
        return 0
    c = yaml.safe_load(P.read_text()) or {}
    links = [l for l in c.get("links") or [] if l.get("name") != (argv[1] if len(argv) > 1 else None)]
    if argv[0] == "add":
        name, url, icon, desc = argv[1:5]
        import os
        item = {"name": name, "url": url, "icon": icon, "desc": desc, "group": os.environ.get("LINK_GROUP", "Servicios")}
        if len(argv) > 5 and argv[5]:
            item["check"] = argv[5]
        links.append(item)
    elif argv[0] != "remove":
        print(__doc__)
        return 2
    c["links"] = links
    with open(P, "w") as f:                       # mismo archivo (mismo inodo: lo monta Docker)
        yaml.safe_dump(c, f, sort_keys=False, allow_unicode=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
