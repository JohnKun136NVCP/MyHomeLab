"""Adivina el tipo de dispositivo por fabricante (OUI), nombre y puertos abiertos."""
from __future__ import annotations

TYPES = ["Teléfono", "Computadora", "Cámara", "TV / Streaming", "Consola", "Impresora",
         "Access Point / Router", "IoT / Domótica", "Servidor", "Tablet", "Desconocido"]

ICONS = {"Teléfono": "📱", "Computadora": "💻", "Cámara": "📷", "TV / Streaming": "📺",
         "Consola": "🎮", "Impresora": "🖨", "Access Point / Router": "📡",
         "IoT / Domótica": "💡", "Servidor": "🖥", "Tablet": "📲", "Desconocido": "❔"}

# (palabra en fabricante u hostname en minúsculas, tipo)
HOSTNAME_RULES = [
    (("iphone", "android", "galaxy", "redmi", "pixel", "moto", "huawei-", "oppo", "vivo-",
      "poco", "xiaomi-", "honor"), "Teléfono"),
    (("ipad", "tab-", "galaxy-tab", "fire-"), "Tablet"),
    (("tapo", "c200", "c210", "c310", "imou", "ezviz", "ipc", "cam"), "Cámara"),
    (("tv", "roku", "chromecast", "firetv", "aft", "bravia", "webos", "tizen", "shield"),
     "TV / Streaming"),
    (("ps4", "ps5", "playstation", "xbox", "switch", "nintendo"), "Consola"),
    (("printer", "epson", "brother", "canon", "hp-", "npi"), "Impresora"),
    (("desktop-", "laptop", "macbook", "imac", "-pc", "thinkpad", "ubuntu", "fedora", "arch"),
     "Computadora"),
    (("raspberrypi", "server", "nas", "proxmox", "truenas"), "Servidor"),
    (("esp", "tuya", "shelly", "sonoff", "smartplug", "bulb", "alexa", "echo", "google-home",
      "nest"), "IoT / Domótica"),
]
VENDOR_RULES = [
    (("tenda", "ubiquiti", "mikrotik", "tp-link systems", "netgear", "zte", "huawei technologies",
      "arris", "technicolor", "sagemcom", "askey"), "Access Point / Router"),
    (("apple", "samsung", "xiaomi", "motorola", "oneplus", "oppo", "vivo", "realme", "honor",
      "huawei device", "google"), "Teléfono"),
    (("hangzhou hikvision", "ezviz", "dahua", "imou", "reolink", "wyze"), "Cámara"),
    (("lg electronics", "roku", "tcl", "hisense", "vizio", "amazon"), "TV / Streaming"),
    (("sony interactive", "nintendo", "microsoft"), "Consola"),
    (("seiko epson", "brother", "canon", "hewlett packard", "hp inc", "xerox"), "Impresora"),
    (("espressif", "tuya", "shelly", "itead", "sonoff", "signify", "philips lighting"),
     "IoT / Domótica"),
    (("raspberry pi", "synology", "qnap"), "Servidor"),
    (("intel", "dell", "lenovo", "asustek", "micro-star", "gigabyte", "acer", "liteon",
      "azurewave", "realtek", "hon hai", "quanta", "pegatron", "chicony", "rivet"), "Computadora"),
]


def classify(vendor: str, hostname: str, ports: list[str], random_mac: bool = False,
             is_infra: bool = False) -> str:
    v = (vendor or "").lower()
    h = (hostname or "").lower()
    pnums = {p.split("/")[0] for p in ports}
    if is_infra:
        return "Access Point / Router"
    # Los puertos son la pista más confiable
    if "554" in pnums:  # RTSP
        return "Cámara"
    if pnums & {"631", "9100", "515"}:
        return "Impresora"
    if pnums & {"8008", "8009", "8060", "7000", "8001", "8002", "1925", "3000"} and not pnums & {"22", "445"}:
        return "TV / Streaming"
    if pnums & {"3389", "445", "139", "5900", "548"}:
        return "Computadora"
    if pnums & {"22"} and pnums & {"80", "443", "8080", "9000", "9090", "3000", "5000"}:
        return "Servidor"
    for keys, t in HOSTNAME_RULES:
        if any(k in h for k in keys):
            return t
    # TP-Link: Tapo es cámara si abre RTSP, si no es enchufe/foco inteligente
    if "tp-link" in v or "tp link" in v:
        return "IoT / Domótica"
    for keys, t in VENDOR_RULES:
        if any(k in v for k in keys):
            return t
    if random_mac:
        return "Teléfono"  # MAC privada sin más pistas: casi siempre un celular
    if pnums & {"22"}:
        return "Servidor"
    return "Desconocido"
