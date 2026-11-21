# Seguridad: cuentas, firewall, SSH con llave y certificado

## Cuentas para toda la casa (dashboard → Cuentas)
| Rol | Qué puede |
|---|---|
| **Administrador** | todo |
| **Ve registros** (tú lo das) | todo en modo lectura: Resumen, Dispositivos, Tráfico, Conexiones, Mapa, Eventos… y recibe las alertas del bot si vincula su Telegram. No ve Ajustes, VPN, ACL, Sistema, Cuentas ni `/vault/admin` |
| **Familia** | solo **Mis respaldos**, Cámaras, Servicios y Accesos. Recibe únicamente los avisos de sus propios respaldos ([respaldos de la familia](respaldos-familia.md)) |

- La cuenta **principal** es la del `.env` (`WEB_USER`, normalmente `admin`). Su contraseña
  se cambia en **Mi cuenta** (clic en tu nombre abajo a la izquierda) o con `./hl password`.
- Crea una cuenta por persona: **Cuentas → Nueva cuenta**. Cada quien cambia la suya en Mi cuenta.
- Cambiar la contraseña de alguien, o borrar su cuenta, **cierra sus sesiones** al instante.
- Los permisos se revisan en el servidor en cada petición (Caddy pregunta a netwatch),
  no solo se esconden botones.

### Aviso de inicio de sesión
Cuando alguien entra **desde una IP en la que nunca había entrado** con esa cuenta, el bot te
avisa (👤) con la IP y, si la conoce, el nombre del equipo. 5 contraseñas equivocadas desde la
misma IP la bloquean 10 minutos y también te avisa (🔑). Se apaga en Ajustes → Avisos.

## Firewall de la Pi y SSH solo con llave
Todo se configura en `config/homelab.yml`:
```yaml
firewall:
  lan_networks: [192.168.1.0/24, 192.168.100.0/24]   # quién abre el dashboard y usa el DNS
  ssh:
    interface: wlan0            # SSH solo entra por la red del laboratorio
    allow:                      # tus 3 equipos (IP fija en la Tenda del laboratorio)
      - {ip: 192.168.100.10, nombre: laptop}
      - {ip: 192.168.100.11, nombre: pc-lab}
      - {ip: 192.168.100.12, nombre: tablet}
  extra: []                     # reglas tuyas, ej. ["allow 8090/tcp comment nginx"]
```

### Paso 1: pon tu llave en la Pi (desde cada uno de los 3 equipos)
```bash
ssh-keygen -t ed25519                 # si ese equipo aún no tiene llave
ssh-copy-id TU_USUARIO@192.168.100.X  # IP de la Pi en el laboratorio (wlan0)
ssh TU_USUARIO@192.168.100.X          # debe entrar SIN pedir contraseña
```

### Paso 2: aplica el firewall (desde uno de esos equipos)
```bash
./hl firewall               # te muestra el plan y pregunta
./hl firewall --key-only  # además apaga el acceso con contraseña
```
- Solo **agrega** reglas; las tuyas (nginx 80/443, 8090…) se quedan. Quita las reglas
  «SSH desde cualquier lado».
- Usa el puerto en el que **de verdad** escucha tu SSH (lo detecta con `sshd -T`, o el socket
  en Ubuntu reciente). Si quieres fijarlo: `firewall.ssh.port: 2222` en `homelab.yml`.
  `./hl check` te avisa si UFW no tiene regla para ese puerto.
- Si estás conectado desde un equipo que no está en la lista, **se detiene** para no dejarte fuera.
- `--key-only` revisa primero que tu usuario tenga llaves en `~/.ssh/authorized_keys`; si no,
  no cambia nada. Escribe `/etc/ssh/sshd_config.d/10-homelab.conf` (sin contraseña, sin root).
  **No cierres la sesión**: abre otra terminal y prueba entrar. Para deshacer:
  `sudo rm /etc/ssh/sshd_config.d/10-homelab.conf && sudo systemctl reload ssh`.

Resultado: para entrar por SSH hace falta **las dos cosas**: estar en uno de tus 3 equipos
(IP permitida, por `wlan0`) **y** tener su llave. Una contraseña robada ya no sirve.

> Los puertos que publica Docker (AdGuard 3002, Uptime Kuma 3001, el dashboard) no pasan por
> UFW: se pueden abrir desde la red local aunque UFW no tenga regla. Por eso el dashboard tiene
> su propio inicio de sesión y AdGuard/Uptime Kuma el suyo.

## Certificado del homelab
El dashboard usa un certificado propio de Caddy; por eso el navegador dice «no seguro».
Instálalo una vez en cada equipo y desaparece el aviso:

1. En la Pi: `./hl ca`.
2. En cada equipo: abre `https://IP-de-la-Pi:PUERTO/ca.crt` (o Sistema → Descargar certificado).

| Sistema | Cómo instalarlo |
|---|---|
| Windows | doble clic → Instalar certificado → Equipo local → «Entidades de certificación raíz de confianza» |
| macOS | doble clic (Llavero) → abre el certificado → Confiar → «Confiar siempre» |
| Linux (Ubuntu/Debian) | `sudo cp homelab-ca.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates`; Firefox: Ajustes → Certificados → Importar |
| Android | Ajustes → Seguridad → Cifrado y credenciales → Instalar certificado → Certificado de CA |
| iPhone | ábrelo en Safari → Ajustes → Perfil descargado → Instalar; luego General → Información → Confianza de certificados → actívalo |

Las apps de Bitwarden (Vaultwarden) y los clientes de respaldo también funcionan mejor con él.

## Vaultwarden para la familia
Ver [servicios](servicios.md#cuentas-para-la-familia-cada-quien-su-bóveda): registros cerrados,
cada quien por invitación, y una Organización «Casa» para compartir contraseñas.

## CrowdSec: bloquea a quien ataca

```bash
sudo ./hl crowdsec            # instala CrowdSec + el bloqueador de nftables
sudo ./hl crowdsec status     # bloqueos activos, últimas alertas y qué está leyendo
sudo ./hl crowdsec unban IP   # desbloquear (por si fuiste tú)
sudo ./hl crowdsec enroll CLAVE   # opcional: consola web gratis en app.crowdsec.net
sudo ./hl crowdsec remove
```

Lee el SSH (journald) y la bitácora de Caddy (el panel). Si alguien prueba contraseñas, escanea
rutas o explota una CVE conocida, lo bloquea en el firewall unas horas y te avisa por Telegram
(«🛑 CrowdSec bloqueó …»). También usa la lista comunitaria de IPs maliciosas.

- Su API escucha solo en `127.0.0.1:8085` (el 8080 es del bot).
- **Nunca** bloquea tus redes de casa ni la VPN (lista blanca); la red del **laboratorio no**
  está en la lista blanca, a propósito: si un equipo del lab ataca a la Pi, se bloquea.
- No toca tu nginx (80/443/8090) ni `wg-hoshimy`; solo agrega su propia tabla de nftables.
- Aparece en el panel → Inicio → Servicios con su punto verde.

## Inventario de servicios y CVE (Seguridad → Inventario)

Una vez a la semana, de madrugada, el panel revisa con `nmap -sV` y el script `vulners` qué
programas y versiones tiene abiertos cada equipo (cámaras, AP, impresoras, la PC…) y busca
vulnerabilidades conocidas. Si aparece una CVE nueva con CVSS ≥ 7 te llega un evento
(🧬 «Versión vulnerable en …»). Botón **Escanear ahora** y **Exportar CSV**. Se ajusta en
`config/homelab.yml`:

```yaml
inventory:
  enabled: true
  every_days: 7
```

Es una revisión ligera por versión; para algo profundo usa Greenbone del laboratorio.

## Control parental (Seguridad → Control parental)

Por equipo (tablet, tele, consola): qué apps o sitios se bloquean (YouTube, TikTok, Roblox,
Fortnite… el catálogo de AdGuard), **a qué horas sí se permiten** cada día, búsqueda segura y
bloqueo de sitios para adultos. Cada regla se guarda como un cliente de AdGuard
(«homelab: NOMBRE») con la MAC y la IP del equipo, y se resincroniza cada 30 min por si cambia
de IP.

Requisitos: perfil `dns` con `ADGUARD_USER`/`ADGUARD_PASSWORD` en `.env`, que el router reparta
la Pi como DNS y que el equipo **no** use «DNS seguro» propio (Chrome/Firefox → Ajustes →
Privacidad → DNS seguro: apagado; en Android «DNS privado: desactivado»). Un niño con datos
móviles o VPN se lo salta: esto cuida la red de la casa, no el celular fuera de ella.

## Revisión rápida
`./hl check` (o dashboard → Sistema) te dice si SSH acepta contraseña, si UFW está
apagado, si Vaultwarden deja registrarse a cualquiera y cuándo fue el último respaldo.

## Contraseñas de los servicios (panel → Contraseñas)

Si olvidas la de un servicio, el panel genera una nueva, la aplica y te la muestra **una sola
vez** (no queda en la bitácora). Lo mismo en la terminal:

```bash
./hl passwords                              # cuáles se pueden y si están encendidos
sudo ./hl passwords reset grafana
sudo ./hl passwords reset homeassistant TU_USUARIO
```

| Servicio | Qué cambia |
|---|---|
| Grafana | usuario `admin` (y `GRAFANA_ADMIN_PASSWORD` en `.env`) |
| AdGuard Home | el usuario de `ADGUARD_USER` (y `ADGUARD_PASSWORD`, para que el panel siga entrando) |
| Vaultwarden | solo el token de `/vault/admin`; las bóvedas y sus contraseñas maestras no se tocan |
| Home Assistant | el usuario que digas (o el primero); se reinicia |
| Paperless | `PAPERLESS_ADMIN_USER` o el que digas |
| Syncthing | acceso a su página (usuario `admin`) |
| Umami | `admin` o el que digas |
| Immich | el administrador; Immich genera la contraseña |

Las cuentas del panel —que también son las de Authelia— se cambian en **Cuentas**; la del
admin de la web con `./hl password`. Speedtest: `./hl speedtest-login`.

## Trampa en la red (honeypot)

```bash
sudo ./hl honeypot            # la enciende
./hl honeypot status          # señuelos y quién cayó
sudo ./hl honeypot test       # aviso de prueba (para ver que llega a Telegram)
sudo ./hl honeypot remove
```

La Pi finge ser un NAS viejo llamado **nas-respaldos** con FTP, Telnet, SSH, MySQL, RDP, VNC,
Redis, MSSQL y una página de acceso (OpenCanary). Nadie de la casa tiene por qué tocarlos: si
algo se conecta es un equipo infectado, alguien en tu Wi-Fi que no debería, o alguien
escaneando. Llega **🍯 Honeypot: EQUIPO tocó Telnet** en Eventos y por Telegram, con el
usuario y la contraseña que probó.

- Elige puertos libres (si tu SSH real no usa el 22, el señuelo SSH se pone ahí; tu SSH no se
  toca) y abre UFW **solo** para tus redes (`lan_networks`).
- No cuenta lo que hace la propia Pi (inventario y escaneos).
- Si usas Kali o nmap desde el laboratorio contra la Pi, va a sonar: es justo lo que debe pasar.

## Actualizar todo y revisar vulnerabilidades

```bash
./hl update --all             # o Sistema → «Actualizar todo»
./hl update --all --no-scan
./hl scan                     # solo revisar (o Sistema → «Solo revisar imágenes»)
```

`--all` hace `git pull`, baja las imágenes nuevas de la Pi, de las herramientas de la casa y de
Immich, reconstruye el panel y el bot desde cero (Python y paquetes al día), borra las imágenes
viejas y revisa con **Trivy** las imágenes que están corriendo. Solo cuenta vulnerabilidades
**altas y críticas que ya tienen arreglo** (las que se quitan actualizando). El resumen llega por
Telegram y queda en panel → Inventario → «Imágenes de Docker».

GitHub: `.github/dependabot.yml` revisa cada sábado los paquetes de Python, la imagen base de
los Dockerfiles y las imágenes con versión fija de los compose, y abre un pull request.
Activa además **Settings → Code security → Dependabot alerts** en el repositorio.

El sistema de la Pi va aparte: `sudo apt update && sudo apt full-upgrade` (y
`unattended-upgrades` para los parches de seguridad diarios).

## Bitácora de equipos (syslog)

El panel recibe el registro remoto de tus equipos en **UDP 514**: en el módem y cada Tenda
busca «Registro del sistema / Syslog remoto» y pon la IP de la Pi. Se ve en **Bitácora de
equipos** (filtro por equipo y búsqueda) y si un equipo reporta intentos de entrar con
contraseña mala te llega un aviso 📜. `sudo ./hl firewall` abre el 514 solo para tus redes.

## Captura de tráfico (.pcap)

**Captura de tráfico** (solo admin): eliges equipo, interfaz, puerto y duración y bajas el
`.pcap` para Wireshark. Sin modo puente la Pi solo ve lo que pasa por ella (consultas DNS a
AdGuard, difusiones, mDNS); con modo puente, todo. Se guardan las últimas 20.

## Discos, simulacro y certificados

- `sudo ./hl disks --install`: revisa SMART y espacio cada día (sectores malos, temperatura,
  desgaste) y avisa si algo falla. Sistema → «Revisar los discos».
- `sudo ./hl test-backup --install`: simulacro mensual de restauración. Sistema → «Probar el
  último respaldo».
- `watch: {domains: [miempresa.mx]}` en homelab.yml: aviso si el certificado vence en menos
  de 21 días o si cambia a dónde apunta tu dominio.

## Resúmenes por Telegram

- **Domingo**: resumen de seguridad (equipos nuevos, trampa, routers nuevos, CVE, imágenes,
  discos, certificados, caídas). Al momento: `/semanal`.
- **Día 1**: caídas del internet del mes, para reclamarle al proveedor. Al momento: `/caidas`.
