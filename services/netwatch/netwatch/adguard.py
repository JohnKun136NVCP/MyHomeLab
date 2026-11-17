"""AdGuard Home: estadísticas de DNS, nombres de clientes y dominios peligrosos bloqueados."""
from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger("netwatch.adguard")

DANGER = ("blocked_safebrowsing", "blocked_parental")

# DNS de salida (lo que AdGuard le pregunta a internet). Control D gratis por DoH:
# https://controld.com/free-dns
UPSTREAMS = {
    "": ("Lo que tenga AdGuard (no tocar)", []),
    "controld-p1": ("Control D: bloquea malware", ["https://freedns.controld.com/p1"]),
    "controld-p2": ("Control D: malware + anuncios", ["https://freedns.controld.com/p2"]),
    "controld-family": ("Control D familiar: malware, anuncios y contenido para adultos",
                        ["https://freedns.controld.com/family"]),
    "controld-custom": ("Control D con tu perfil (ID del resolver)", []),
    "quad9": ("Quad9 (bloquea malware)", ["https://dns.quad9.net/dns-query"]),
    "cloudflare": ("Cloudflare (sin filtro)", ["https://cloudflare-dns.com/dns-query"]),
}


def upstream_urls(choice: str, resolver_id: str = "") -> list[str]:
    if choice == "controld-custom":
        rid = "".join(c for c in (resolver_id or "") if c.isalnum())
        return [f"https://dns.controld.com/{rid}"] if rid else []
    return list(UPSTREAMS.get(choice, ("", []))[1])


class AdGuard:
    DEFAULT_URL = "http://127.0.0.1:3002"

    def __init__(self, url: str | None, user: str = "", password: str = ""):
        # Sin «dns.adguard_url» en homelab.yml: se prueba el de la Pi y se usa si responde.
        # Con «adguard_url: ""» queda apagado a propósito.
        self.auto = url is None
        self.url = (self.DEFAULT_URL if url is None else url or "").rstrip("/")
        self.reachable = False
        self.user = user or os.environ.get("ADGUARD_USER", "")
        self.password = password or os.environ.get("ADGUARD_PASSWORD", "")
        self.stats: dict = {}
        self.names: dict[str, str] = {}     # ip -> nombre que conoce AdGuard
        self.error = ""
        self._seen: set[str] = set()
        self._first = True    # al arrancar no reavisamos lo que ya estaba en el historial
        self.upstream: list[str] = []

    @property
    def enabled(self) -> bool:
        return bool(self.url) and (self.reachable or not self.auto)

    def _get(self, path: str):
        req = urllib.request.Request(self.url + path)
        if self.user:
            tok = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
            req.add_header("Authorization", f"Basic {tok}")
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read() or b"null")

    def poll(self) -> list[dict]:
        """Actualiza estadísticas y nombres. Regresa bloqueos peligrosos nuevos."""
        if not self.url:
            return []
        try:
            self.stats = self._get("/control/stats") or {}
            clients = self._get("/control/clients") or {}
            for c in clients.get("auto_clients") or []:
                if c.get("ip") and c.get("name") and c["name"] != c["ip"]:
                    self.names[c["ip"]] = c["name"].split(".")[0]
            for c in clients.get("clients") or []:          # los que tú nombraste en AdGuard
                for ip in c.get("ids") or []:
                    if c.get("name"):
                        self.names[ip] = c["name"]
            danger = []
            for status in DANGER:
                for e in (self._get(f"/control/querylog?response_status={status}&limit=50") or {}).get("data", []):
                    key = f"{e.get('time')}|{e.get('client')}|{(e.get('question') or {}).get('name')}"
                    if key in self._seen:
                        continue
                    self._seen.add(key)
                    danger.append({"client": e.get("client", ""), "domain": (e.get("question") or {}).get("name", ""),
                                   "reason": "malware/phishing" if status == "blocked_safebrowsing"
                                   else "control parental", "time": e.get("time", "")})
            self.reachable = True
            if self._first:
                self._first, danger = False, []
            if len(self._seen) > 5000:
                self._seen = set(list(self._seen)[-1000:])
            self.error = ""
            return danger
        except urllib.error.HTTPError as e:
            self.reachable = True      # responde, pero no nos deja entrar
            self.error = ("AdGuard pide usuario y contraseña: pon ADGUARD_USER y ADGUARD_PASSWORD en Ajustes"
                          if e.code in (401, 403) else f"AdGuard respondió {e.code}")
            return []
        except (urllib.error.URLError, OSError, ValueError) as e:
            self.error = f"AdGuard no responde en {self.url}: {e}"
            return []

    def _post(self, path: str, body: dict) -> None:
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        if self.user:
            tok = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
            req.add_header("Authorization", f"Basic {tok}")
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()

    def get_upstream(self) -> list[str]:
        try:
            return list((self._get("/control/dns_info") or {}).get("upstream_dns") or [])
        except (urllib.error.URLError, OSError, ValueError):
            return []

    def set_upstream(self, urls: list[str]) -> tuple[bool, str]:
        """Cambia el DNS de salida de AdGuard. Regresa (ok, mensaje)."""
        if not urls:
            return True, "sin cambios"
        if not self.url:
            return False, "AdGuard no está configurado"
        try:
            if sorted(self.get_upstream()) == sorted(urls):
                return True, "ya estaba así"
            self._post("/control/dns_config", {"upstream_dns": urls})
            self.upstream = urls
            return True, "DNS de salida actualizado"
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return False, "AdGuard no aceptó el usuario/contraseña (Ajustes → AdGuard)"
            return False, f"AdGuard respondió {e.code}: {e.read()[:200]!r}"
        except (urllib.error.URLError, OSError) as e:
            return False, f"AdGuard no responde: {e}"

    def top_clients(self) -> list[tuple[str, int]]:
        out = []
        for item in self.stats.get("top_clients") or []:
            for ip, n in item.items():
                out.append((ip, int(n)))
        return out
