# Herramientas para la casa

Se encienden y apagan una por una, en la Pi o en cualquier PC del homelab:

```bash
./hl home                       # qué hay, qué está encendido y dónde entrar
./hl home enable navidrome      # crea carpetas y contraseñas, abre el firewall y pone el botón
./hl home disable navidrome     # la apaga; sus datos se quedan
```

O en el menú: `./hl` → **c**. Con varias máquinas, `./hl roles` las pone donde digas
([reparto](redistribucion.md)). Los datos van en `HOME_DATA` (por defecto `/srv/homelab-home`;
ponlo en el disco grande editando `hosts/home/.env`).

| Herramienta | Nombre | Web | Para qué |
|---|---|---|---|
| Home Assistant | `homeassistant` | `:8123` | Focos, enchufes y cámaras; automatizaciones («apaga todo a la 1 am») |
| Paperless-ngx | `paperless` | `:8010` | Escanea recibos, actas y contratos; los encuentra por texto (OCR en español) |
| Navidrome | `navidrome` | `:4533` | Tu música en streaming; apps Subsonic en el celular |
| Syncthing | `syncthing` | `:8384` | Carpetas sincronizadas entre tus equipos sin nube |
| Umami | `umami` | `:3007` | Visitas a tu página web sin Google Analytics |
| Túnel de Cloudflare | `cloudflared` | — | Publica Umami a internet sin abrir puertos |
| DNS de respaldo | `dns-backup` | `:3002` | Segundo AdGuard (va en otra máquina, no en la Pi) |

Todas aparecen en el panel (**Inicio** y **Servicios**) con su punto verde o rojo.

## Primeros pasos de cada una

**Home Assistant.** Abre `http://IP:8123` (la primera vez tarda ~2 min), crea tu usuario y
agrega dispositivos. Muchos focos y enchufes Wi‑Fi aparecen solos; las cámaras Tapo e Imou
tienen integración.

**Paperless.** El usuario es `admin` y la contraseña está en `hosts/home/.env`
(`PAPERLESS_ADMIN_PASSWORD`); cámbiala en la web. Para meter documentos:
súbelos en la web, o déjalos en la carpeta `HOME_DATA/paperless/consume` (puedes compartirla
con Syncthing para escanear desde el celular). Trabaja de a uno en la Pi para no saturarla.

**Navidrome.** Te pregunta la carpeta de tu música. En la web crea tu usuario. En el celular
usa una app Subsonic (Symfonium o Substreamer en Android, play:Sub o Amperfy en iPhone) con
`http://IP:4533`.

**Syncthing.** Ponle usuario y contraseña a la web (Acciones → Ajustes → Interfaz). Instala
Syncthing en tus otros equipos (en Android: «Syncthing-Fork») y compárteles carpetas, por
ejemplo tu bóveda de Obsidian.

## Analítica de tu página (Umami)

1. `./hl home enable umami` → entra a `http://IP:3007` con `admin` / `umami` y **cambia la
   contraseña**.
2. **Sitios → Agregar sitio** con tu dominio. Copia el `<script>` que te da.
3. El script se carga en el navegador de cada visitante, así que Umami tiene que ser alcanzable
   desde internet. Sin tocar el VPS: un **túnel de Cloudflare** (gratis, necesita que tu dominio
   use Cloudflare):
   - Cloudflare → **Zero Trust → Networks → Tunnels → Create a tunnel** → copia el token.
   - En «Public hostname»: `visitas.tudominio.com` → servicio `http://umami:3000`.
   - `./hl home enable cloudflared` y pega el token.
4. Pega en tu página el `<script>` cambiando la dirección por `https://visitas.tudominio.com`.

Umami no usa cookies ni guarda datos personales: no necesitas aviso de cookies por él. El túnel
solo publica Umami; el resto del homelab sigue cerrado.

Si algún día prefieres usar el VPS, basta una línea de nginx que reenvíe
`visitas.tudominio.com` por el túnel WireGuard a `http://IP-de-la-Pi:3007` (no ocupa espacio
en el VPS).

## DNS de respaldo

En una máquina que **no** sea la Pi: `./hl home enable dns-backup` (o el rol `dns_backup`).
Copia la configuración del AdGuard de la Pi cada 10 minutos. Si el puerto 53 lo ocupa
`systemd-resolved` (Ubuntu), el script te dice cómo liberarlo. Luego en el módem o en las
Tenda pon como DNS 1° la Pi y 2° esta máquina.
