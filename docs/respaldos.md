# Respaldos de todos tus equipos

Cada equipo comprime sus cosas en un `.7z` cifrado (AES-256, partes de 1.9 GB) y lo sube a
la Pi con `PUT https://IP-de-la-Pi/api/backup/<dispositivo>/<archivo>`. La Pi:

1. revisa el token de **ese** dispositivo (`BACKUP_TOKEN_<NOMBRE>` en `.env`);
2. lo guarda en `BACKUPS_DIR/<dispositivo>/<fecha>/` (las partes del mismo respaldo juntas);
3. borra los respaldos viejos: deja `keep_per_device` y borra los de más de `max_age_days`
   (14 días por defecto; siempre queda el más reciente). Se revisa a diario;
4. te lo reenvía por Telegram con tu Bot API local (cada parte ≤ 1.9 GB) y te avisa.

| Dispositivo | Cliente | Qué guarda |
|---|---|---|
| Linux | Linux Guardian (GUI) | paquetes apt/Flatpak/Snap, apps, temas, SDDM, dotfiles, .ssh… y `restore.sh` |
| Windows | `Respaldo-Homelab.ps1` | `winget export` (reinstala todo con `winget import`), lista de programas, carpetas y configs |
| macOS | `respaldo-homelab.sh` | `Brewfile`, apps de la App Store, `/Applications`, dotfiles, fuentes, carpetas |
| Android | `respaldo-termux.sh` o mandarle archivos al bot | Cámara, Documentos, Descargas, WhatsApp, lista de apps |
| iPhone | mandarle archivos al bot | lo que compartas desde Fotos/Archivos |

## Instalar el cliente (para no buscar la carpeta cada vez)
Cada equipo solo necesita su script (no el repo completo). Una vez configurado:

| Sistema | Comando | Queda |
|---|---|---|
| Linux | `clients/linux-guardian/install.sh` | «Linux Guardian» en el menú y el comando `linux-guardian` |
| Windows | `.\Respaldo-Homelab.ps1 -Instalar` | «Respaldo MyHomeLab» en Inicio y en el Escritorio (y uno «(configurar)») |
| macOS | `./respaldo-homelab.sh --install` | comando `respaldo-homelab` e icono en el Escritorio |
| Android | `bash respaldo-termux.sh --install` | comando `respaldo-homelab` y botón en la pantalla de inicio con Termux:Widget |

## Agregar un dispositivo
1. En `config/homelab.yml` → `backups.devices`: `- {name: laptop-trabajo, os: windows}`
2. En `.env`: `BACKUP_TOKEN_LAPTOP_TRABAJO=` y corre `./scripts/gen-secrets.sh` (lo llena).
3. Agrega la variable al servicio `bot` en `hosts/pi/docker-compose.yml` y reinicia.

## Certificado
La web usa un certificado local de Caddy. Los clientes funcionan sin instalarlo (no
verifican), pero es mejor ponerlo en cada cliente (`ca` / `CA` / «Certificado CA»):
`./hl ca` en la Pi y luego descárgalo desde el dashboard (Sistema → Descargar certificado,
o `https://IP-de-la-Pi:PUERTO/ca.crt`). Cómo instalarlo en cada sistema: [seguridad](seguridad.md#certificado-del-homelab).

## Respaldo del homelab
Además de respaldar tus computadoras, el homelab se respalda a sí mismo (si la SSD o la Pi
mueren, lo restauras en minutos):

| Incluye | No incluye |
|---|---|
| `.env`, `config/`, Vaultwarden, cuentas y dispositivos del dashboard, bot (publicaciones programadas), AdGuard, Uptime Kuma, Grafana, certificado de Caddy | los respaldos de tus equipos (ya son respaldos), historial de Prometheus, multimedia ya publicada |

```bash
./hl backup                                   # ahora (o dashboard → Sistema → Respaldar ahora)
sudo ./scripts/backup.sh --install   # cada domingo 04:30
```
- Va **cifrado** (AES-256) con `HOMELAB_BACKUP_PASSWORD` del `.env`; `gen-secrets.sh` la
  genera y te la muestra una vez. **Guárdala en Vaultwarden y en papel**: sin ella no se abre.
- Se guarda en `BACKUPS_DIR/homelab` (tu SSD), te llega por Telegram y, si pones
  `homelab_backup.vps: usuario@ip:/carpeta`, también al VPS. Se quedan los últimos 8.
- Pausa 2–3 s Vaultwarden, Uptime Kuma, netwatch, bot y Grafana para copiar sus bases sin dañarlas.

**Restaurar** (Pi nueva o SSD nueva):
```bash
git clone https://github.com/JohnKun136NVCP/MyHomeLab && cd MyHomeLab
./scripts/install-pi.sh         # o solo instala Docker
./hl restore ~/homelab-raspberrypi-20261122-0430.tar.gz.enc
```
Antes de pisar algo guarda lo actual en `run/antes-de-restaurar-FECHA/`.

## Recordatorio
Windows (tarea cada 12 semanas), macOS (launchd, cada mes revisa 90 días), Android
(Termux:API semanal, con Wi-Fi y cargando) y Linux Guardian (temporizador de systemd). El
reporte diario del bot marca con ⚠ los dispositivos sin respaldo en más de 90 días.
