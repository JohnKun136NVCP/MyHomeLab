"""«Cine en casa» (services/cine): que el JS sea válido y no use la autenticación vieja de Jellyfin.

Jellyfin 12 desactivó api_key, X-Emby-Authorization y las rutas /Users/{id}/Items…: si alguien
las vuelve a meter, el portal dejaría de entrar a los servidores actualizados.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

CINE = Path(__file__).resolve().parents[1] / "services" / "cine"


def test_js_valido():
    if not shutil.which("node"):
        pytest.skip("sin node")
    r = subprocess.run(["node", "-e", "new Function(require('fs').readFileSync(process.argv[1],'utf8'))",
                        str(CINE / "cine.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_sin_autenticacion_vieja():
    js = (CINE / "cine.js").read_text()
    assert "api_key" not in js and "X-Emby" not in js
    assert not re.search(r"/Users/\$\{[^}]+\}/(Items|Views|FavoriteItems|PlayedItems|Images)", js)
    assert "ApiKey=" in js and 'MediaBrowser Client=' in js


def test_archivos_y_nginx():
    html = (CINE / "index.html").read_text()
    for f in ("cine.css", "cine.js", "config.js", "manifest.webmanifest", "fonts/fonts.css"):
        assert f in html and (CINE / f).exists(), f
    conf = (CINE.parents[1] / "config" / "cine" / "nginx.conf").read_text()
    assert "proxy_pass http://jellyfin:8096/;" in conf and "proxy_buffering off" in conf
