"""Detecta cámaras genéricas (focos 360°, mini cámaras WiFi) y su ruta RTSP.

Estas cámaras no traen marca: lo que importa es la app con que se configuran.
Probamos las rutas RTSP conocidas de cada «familia» con DESCRIBE (como VLC) y
autenticación Basic/Digest, sin necesitar ffmpeg.
"""
from __future__ import annotations

import base64
import hashlib
import re
import socket
from urllib.parse import quote

# (familia, app con la que se configura, puerto, ruta HD, ruta SD, usuario por defecto, nota)
FAMILIES = [
    ("iCSee / XMEye (Xiongmai)", "iCSee, XMEye", 554,
     "/user={u}&password={p}&channel=1&stream=0.sdp?real_stream",
     "/user={u}&password={p}&channel=1&stream=1.sdp?real_stream", "admin",
     "Usuario admin; la contraseña es la que pusiste en la app (de fábrica va vacía)."),
    ("V380 / V380 Pro", "V380, V380 Pro", 554, "/live/ch00_0", "/live/ch00_1", "admin",
     "En la app V380: ⚙ → Configuración → RTSP/ONVIF → actívalo y pon contraseña."),
    ("Yoosee / CamHi Yoosee", "Yoosee", 554, "/onvif1", "/onvif2", "admin",
     "Usuario admin; la contraseña es la «contraseña del dispositivo» de la app (no la de tu cuenta)."),
    ("CamHi / HiP2P", "CamHi, CamHipro", 554, "/11", "/12", "admin",
     "Usuario y contraseña de la cámara (de fábrica admin/admin)."),
    ("Genérica ONVIF", "varias", 554, "/stream1", "/stream2", "admin", ""),
    ("Genérica H.264", "varias", 554, "/h264/ch1/main/av_stream", "/h264/ch1/sub/av_stream", "admin", ""),
    ("Genérica", "varias", 554, "/live/main", "/live/sub", "admin", ""),
    ("Genérica", "varias", 554, "/live", "", "admin", ""),
    ("Genérica", "varias", 554, "/ch0_0.h264", "/ch0_1.h264", "admin", ""),
    ("V380 (puerto 10554)", "V380", 10554, "/tcp/av0_0", "/tcp/av0_1", "admin", ""),
    ("Genérica", "varias", 8554, "/profile0", "/profile1", "admin", ""),
    ("Genérica", "varias", 554, "/", "", "admin", ""),
]
HINT_PORTS = {554: "RTSP", 8554: "RTSP", 10554: "RTSP (V380)", 8899: "ONVIF (iCSee/XMEye)",
              34567: "iCSee/XMEye", 6668: "Tuya / Smart Life", 80: "web", 8000: "ONVIF/HTTP"}
TUYA_NOTE = ("Parece una cámara Tuya / Smart Life (puerto 6668). Casi ninguna trae RTSP: solo funciona "
             "con su app y la nube. Si la app tiene «ONVIF» o «RTSP» en ajustes avanzados, actívalo y vuelve a probar.")


def open_ports(ip: str, ports=tuple(HINT_PORTS), timeout: float = 1.0) -> list[int]:
    out = []
    for p in ports:
        try:
            with socket.create_connection((ip, p), timeout=timeout):
                out.append(p)
        except OSError:
            pass
    return out


def _parse_auth(header: str) -> dict:
    return {k.lower(): v for k, v in re.findall(r'(\w+)="?([^",]*)"?', header)}


def _request(ip: str, port: int, url: str, cseq: int, auth: str = "", timeout: float = 4) -> tuple[int, dict]:
    msg = (f"DESCRIBE {url} RTSP/1.0\r\nCSeq: {cseq}\r\nAccept: application/sdp\r\n"
           f"User-Agent: MyHomeLab\r\n{auth}\r\n")
    with socket.create_connection((ip, port), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall(msg.encode())
        data = b""
        while b"\r\n\r\n" not in data and len(data) < 16384:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
    head = data.split(b"\r\n\r\n", 1)[0].decode(errors="replace").split("\r\n")
    m = re.match(r"RTSP/1\.\d (\d{3})", head[0] if head else "")
    code = int(m.group(1)) if m else 0
    headers: dict[str, str] = {}
    for line in head[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers.setdefault(k.strip().lower(), v.strip())
    return code, headers


def describe(ip: str, port: int, path: str, user: str, pw: str) -> int:
    """Código RTSP de DESCRIBE (200 = la ruta sirve video con esas credenciales)."""
    url = f"rtsp://{ip}:{port}{path}"
    code, h = _request(ip, port, url, 1)
    if code != 401:
        return code
    www = h.get("www-authenticate", "")
    if www.lower().startswith("digest"):
        a = _parse_auth(www)
        ha1 = hashlib.md5(f"{user}:{a.get('realm', '')}:{pw}".encode()).hexdigest()
        ha2 = hashlib.md5(f"DESCRIBE:{url}".encode()).hexdigest()
        resp = hashlib.md5(f"{ha1}:{a.get('nonce', '')}:{ha2}".encode()).hexdigest()
        auth = (f'Authorization: Digest username="{user}", realm="{a.get("realm", "")}", '
                f'nonce="{a.get("nonce", "")}", uri="{url}", response="{resp}"\r\n')
    else:
        auth = "Authorization: Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode() + "\r\n"
    code, _ = _request(ip, port, url, 2, auth)
    return code


def _url(ip, port, path, user, pw, mask=False):
    p = "••••" if (mask and pw) else quote(pw, safe="")
    path = path.format(u=quote(user, safe=""), p=p)
    if "user=" in path:                              # iCSee lleva las credenciales en la ruta
        return f"rtsp://{ip}:{port}{path}"
    cred = f"{quote(user, safe='')}:{p}@" if user else ""
    return f"rtsp://{cred}{ip}:{port}{path}"


def probe(ip: str, user: str = "admin", pw: str = "", name: str = "") -> dict:
    ports = open_ports(ip)
    res = {"ip": ip, "ports": [{"port": p, "what": HINT_PORTS.get(p, "")} for p in ports],
           "found": None, "tried": [], "notes": []}
    if 6668 in ports and not {554, 8554, 10554} & set(ports):
        res["notes"].append(TUYA_NOTE)
    if 8899 in ports or 34567 in ports:
        res["notes"].append("Abre 8899/34567: casi seguro es de la familia iCSee/XMEye.")
    rtsp_ports = [p for p in (554, 10554, 8554) if p in ports]
    if not rtsp_ports:
        res["notes"].append("No tiene ningún puerto RTSP abierto. Activa RTSP/ONVIF en la app de la cámara, "
                            "o puede que solo funcione con su nube.")
        return res
    # iCSee primero si sus puertos están abiertos
    fams = sorted(FAMILIES, key=lambda f: 0 if ("iCSee" in f[0] and (8899 in ports or 34567 in ports)) else 1)
    auth_fail = False
    for fam, app, port, main, sub, def_user, note in fams:
        if port not in rtsp_ports:
            continue
        u = user or def_user
        try:
            code = describe(ip, port, main.format(u=quote(u, safe=""), p=quote(pw, safe="")), u, pw)
        except OSError as e:
            res["tried"].append({"family": fam, "path": main.split("?")[0], "result": f"error: {e}"})
            continue
        res["tried"].append({"family": fam, "path": main.split("?")[0] if "user=" not in main else "/user=…",
                             "result": code})
        if code == 401:
            auth_fail = True
        if code == 200:
            sub_ok = False
            if sub:
                try:
                    sub_ok = describe(ip, port, sub.format(u=quote(u, safe=""), p=quote(pw, safe="")), u, pw) == 200
                except OSError:
                    sub_ok = False
            res["found"] = {
                "family": fam, "app": app, "note": note, "port": port,
                "main": _url(ip, port, main, u, pw), "main_masked": _url(ip, port, main, u, pw, True),
                "sub": _url(ip, port, sub, u, pw) if sub_ok else "",
                "sub_masked": _url(ip, port, sub, u, pw, True) if sub_ok else "",
            }
            res["frigate"] = frigate_snippet(name or f"camara_{ip.split('.')[-1]}", res["found"])
            return res
    if auth_fail:
        res["notes"].append("La cámara pide usuario y contraseña y los que pusiste no funcionaron "
                            "(revisa la contraseña del dispositivo en su app).")
    else:
        res["notes"].append("Tiene RTSP pero ninguna ruta conocida respondió. Busca el modelo exacto en "
                            "la etiqueta o en la app y dímelo, o prueba con ONVIF Device Manager.")
    return res


def frigate_snippet(name: str, f: dict) -> str:
    name = re.sub(r"[^a-z0-9_]", "_", name.lower()) or "camara"
    main = f["main_masked"].replace("••••", "{FRIGATE_" + name.upper() + "_PASSWORD}")
    sub = (f["sub_masked"] or f["main_masked"]).replace("••••", "{FRIGATE_" + name.upper() + "_PASSWORD}")
    return (f"# {f['family']} (app {f['app']}) — pega esto en config/frigate/config.yml\n"
            f"# y pon FRIGATE_{name.upper()}_PASSWORD=… en el .env de la PC.\n"
            f"# Si tu config.yml ya tiene go2rtc/streams y cameras, pega solo las líneas de dentro.\n"
            f"go2rtc:\n  streams:\n    {name}:\n      - {main}\n    {name}_sd:\n      - {sub}\n\n"
            f"cameras:\n  {name}:\n    ffmpeg:\n      inputs:\n"
            f"        - path: rtsp://127.0.0.1:8554/{name}_sd\n          input_args: preset-rtsp-restream\n"
            f"          roles: [detect]\n"
            f"        - path: rtsp://127.0.0.1:8554/{name}\n          input_args: preset-rtsp-restream\n"
            f"          roles: [record]\n"
            f"    detect: {{width: 640, height: 360, fps: 5}}\n")
