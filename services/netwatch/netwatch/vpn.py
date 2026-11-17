"""WireGuard: lee los túneles que ya tienes y crea configuraciones para nuevos equipos.

La Pi es un «peer» del VPS (el VPS es el centro). Para agregar tu teléfono o laptop:
  1. aquí se generan sus llaves, su IP libre y su archivo/QR;
  2. en el VPS se registra su llave pública (te damos los comandos exactos).
Las llaves privadas nunca se guardan: se muestran una sola vez.
"""
from __future__ import annotations

import ipaddress
import re
import shutil
import subprocess
import time


def _run(cmd: list[str], inp: str | None = None, timeout: int = 10) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, input=inp, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip() if p.returncode == 0 else p.stderr.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def available() -> bool:
    return shutil.which("wg") is not None


def parse_dump(text: str) -> dict[str, dict]:
    """`wg show all dump` → {interfaz: {...}}. Descarta las llaves privadas y precompartidas."""
    out: dict[str, dict] = {}
    for line in text.splitlines():
        f = line.split("\t")
        if len(f) == 5:                         # interfaz: iface priv pub port fwmark
            out[f[0]] = {"name": f[0], "public_key": f[2],
                         "listen_port": int(f[3]) if f[3].isdigit() else None, "peers": []}
        elif len(f) == 9 and f[0] in out:      # peer: iface pub psk endpoint allowed hs rx tx keepalive
            out[f[0]]["peers"].append({
                "public_key": f[1],
                "endpoint": "" if f[3] == "(none)" else f[3],
                "allowed_ips": [a for a in f[4].split(",") if a and a != "(none)"],
                "handshake": int(f[5]) if f[5].isdigit() else 0,
                "rx": int(f[6]) if f[6].isdigit() else 0,
                "tx": int(f[7]) if f[7].isdigit() else 0,
                "keepalive": int(f[8]) if f[8].isdigit() else 0})
    return out


def addresses(iface: str) -> list[str]:
    code, out = _run(["ip", "-4", "-o", "addr", "show", "dev", iface], timeout=5)
    return re.findall(r"inet (\d+\.\d+\.\d+\.\d+/\d+)", out) if code == 0 else []


def guess_hub(iface: dict, addrs: list[str]) -> dict:
    """Del túnel que ya tienes deduce: la red, tu IP y la IP del VPS dentro del túnel."""
    res = {"network": "", "my_ip": "", "hub_ip": "", "hub_endpoint": "", "hub_key": "",
           "warnings": []}
    if addrs:
        itf = ipaddress.ip_interface(addrs[0])
        res["my_ip"] = str(itf.ip)
        res["network"] = str(itf.network) if itf.network.prefixlen < 32 else ""
    hub = next((p for p in iface["peers"] if p["endpoint"]), iface["peers"][0] if iface["peers"] else None)
    if not hub:
        return res
    res["hub_endpoint"], res["hub_key"] = hub["endpoint"], hub["public_key"]
    nets = [ipaddress.ip_network(a, strict=False) for a in hub["allowed_ips"] if ":" not in a]
    host32 = [n for n in nets if n.prefixlen == 32]
    if host32:
        res["hub_ip"] = str(host32[0].network_address)
    if not res["network"]:
        wide = [n for n in nets if n.prefixlen < 32]
        if wide:
            res["network"] = str(wide[0])
    if not res["hub_ip"] and res["network"]:
        # Lo más común: el VPS es la primera IP de la red del túnel
        first = next(ipaddress.ip_network(res["network"]).hosts())
        if str(first) != res["my_ip"]:
            res["hub_ip"] = str(first)
    if res["network"] and nets and all(n.prefixlen == 32 for n in nets):
        res["warnings"].append(
            f"El peer del VPS solo acepta {', '.join(map(str, nets))}: tus otros equipos de la VPN no "
            f"podrán hablar con la Pi. Cambia su AllowedIPs a {res['network']} en la config de la Pi.")
    if hub["handshake"] and time.time() - hub["handshake"] > 300:
        res["warnings"].append("El último contacto con el VPS fue hace más de 5 minutos: ¿túnel caído?")
    return res


def status() -> dict:
    if not available():
        return {"available": False, "interfaces": [], "error": "Falta wireguard-tools en el contenedor"}
    code, out = _run(["wg", "show", "all", "dump"])
    if code != 0:
        return {"available": True, "interfaces": [],
                "error": out or "No pude leer WireGuard (¿falta NET_ADMIN?)"}
    ifaces = []
    for name, itf in parse_dump(out).items():
        addrs = addresses(name)
        ifaces.append({**itf, "addresses": addrs, "hub": guess_hub(itf, addrs)})
    return {"available": True, "interfaces": ifaces, "error": ""}


def genkeys() -> tuple[str, str]:
    code, priv = _run(["wg", "genkey"])
    if code != 0:
        raise RuntimeError(f"wg genkey falló: {priv}")
    code, pub = _run(["wg", "pubkey"], inp=priv + "\n")
    if code != 0:
        raise RuntimeError(f"wg pubkey falló: {pub}")
    return priv, pub


def next_free_ip(network: str, used: set[str], start: int = 10) -> str:
    net = ipaddress.ip_network(network)
    for i, h in enumerate(net.hosts(), start=1):
        if i >= start and str(h) not in used:
            return str(h)
    raise RuntimeError(f"No quedan IPs libres en {network}")


def client_config(priv: str, ip: str, hub: dict, dns: str = "") -> str:
    lines = ["[Interface]", f"PrivateKey = {priv}", f"Address = {ip}/32"]
    if dns:
        lines.append(f"DNS = {dns}")
    lines += ["", "[Peer]", f"PublicKey = {hub['hub_key']}",
              f"Endpoint = {hub['hub_endpoint']}",
              f"AllowedIPs = {hub['network']}",
              "PersistentKeepalive = 25", ""]
    return "\n".join(lines)


def vps_commands(iface_vps: str, name: str, pub: str, ip: str) -> str:
    conf = f"/etc/wireguard/{iface_vps}.conf"
    return "\n".join([
        f"# En el VPS (con tus llaves SSH de siempre). Agrega «{name}» sin reiniciar el túnel:",
        f"sudo cp {conf} {conf}.respaldo-$(date +%F)",
        f"sudo wg set {iface_vps} peer {pub} allowed-ips {ip}/32",
        f"printf '\\n[Peer]\\n# {name}\\nPublicKey = {pub}\\nAllowedIPs = {ip}/32\\n' | sudo tee -a {conf}",
        "# Solo la primera vez: que el VPS pase tráfico entre los equipos del túnel",
        "echo 'net.ipv4.ip_forward=1' | sudo tee /etc/sysctl.d/90-wg-forward.conf && sudo sysctl --system",
        f"sudo ufw route allow in on {iface_vps} out on {iface_vps} comment 'peers del túnel'",
    ])


def remove_commands(iface_vps: str, name: str, pub: str) -> str:
    conf = f"/etc/wireguard/{iface_vps}.conf"
    return "\n".join([
        f"# En el VPS: quitar «{name}»",
        f"sudo wg set {iface_vps} peer {pub} remove",
        f"# y borra su bloque [Peer] (# {name}) de {conf} con: sudo nano {conf}",
    ])


def qr_svg(text: str) -> str:
    try:
        import io

        import qrcode
        import qrcode.image.svg
    except ImportError:
        return ""
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()
