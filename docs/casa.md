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
| Mealie | `mealie` | `:9925` | Recetas de la familia (importa de cualquier página) y lista del súper compartida |
| Jellyseerr | `jellyseerr` | `:5055` | La familia pide películas o series; tú apruebas y aparecen en Cine en casa |
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

**Mealie.** Entra a `http://IP:9925` con `changeme@example.com` / `MyPassword` y cámbiala. En
Ajustes → Usuarios crea una cuenta por persona (el registro abierto está apagado). Para guardar
una receta pega el enlace de cualquier página de cocina y la importa sola; la **lista del súper**
la comparte toda la casa y se puede instalar en el celular como app (menú del navegador →
«Agregar a pantalla de inicio»).

**Jellyseerr.** Abre `http://IP:5055`, elige **Jellyfin** y pon la dirección de tu Jellyfin
(`http://IP-de-la-PC:8096`) con tu usuario administrador. La familia entra con su misma cuenta
de Jellyfin, busca una película o serie y la pide; tú la apruebas y, cuando la agregas a la
biblioteca, aparece en Cine en casa. (Sin Radarr/Sonarr solo queda la lista de pedidos: tú
consigues el archivo.)

**Recordatorios de la casa (bot).** No hay que encender nada: en el bot de Telegram cada quien
escribe `/recordar mañana 9:00 pagar la luz`, `/recordar 15/12 18:00 cita con el dentista`,
`/recordar cada mes 5 9:00 pagar el internet`, `/recordar cada año 03-15 cumpleaños de mamá`
o `/recordar en 2h sacar la ropa`. `/recordatorios` los lista y `/olvidar N` borra uno. Cada
quien ve solo los suyos; el aviso llega a la hora (zona de `TZ`).

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
   - Cloudflare te muestra un comando para «instalar el conector» (`cloudflared service install …`
     o `cloudflared tunnel run --token …`). **No hace falta correrlo**: el túnel ya corre en Docker.
     Solo copia el token (el texto largo que empieza con `eyJ`). Si lo corres a mano se «queda
     ahí»: es normal, es el túnel funcionando; al cerrarlo, el túnel se cae.
   - Revisa con `./hl home logs cloudflared`: debe decir *Registered tunnel connection* y en
     Cloudflare el túnel pasa a **HEALTHY**; entonces te deja seguir al «Public hostname».
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
