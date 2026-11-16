# Respaldos de todos tus equipos

Cada equipo comprime sus cosas en un `.7z` cifrado (AES-256, partes de 1.9 GB) y lo sube a
la Pi con `PUT https://IP-de-la-Pi/api/backup/<dispositivo>/<archivo>`. La Pi:

1. revisa el token de **ese** dispositivo (`BACKUP_TOKEN_<NOMBRE>` en `.env`);
2. lo guarda en `BACKUPS_DIR/<dispositivo>/<fecha>/` (las partes del mismo respaldo juntas);
3. borra los respaldos viejos y deja `keep_per_device`;
4. te lo reenvía por Telegram con tu Bot API local (cada parte ≤ 1.9 GB) y te avisa.

| Dispositivo | Cliente | Qué guarda |
|---|---|---|
| Linux | Linux Guardian (GUI) | paquetes apt/Flatpak/Snap, apps, temas, SDDM, dotfiles, .ssh… y `restore.sh` |
| Windows | `Respaldo-Homelab.ps1` | `winget export` (reinstala todo con `winget import`), lista de programas, carpetas y configs |
| macOS | `respaldo-homelab.sh` | `Brewfile`, apps de la App Store, `/Applications`, dotfiles, fuentes, carpetas |
| Android | `respaldo-termux.sh` o mandarle archivos al bot | Cámara, Documentos, Descargas, WhatsApp, lista de apps |
| iPhone | mandarle archivos al bot | lo que compartas desde Fotos/Archivos |

## Agregar un dispositivo
1. En `config/homelab.yml` → `backups.devices`: `- {name: laptop-trabajo, os: windows}`
2. En `.env`: `BACKUP_TOKEN_LAPTOP_TRABAJO=` y corre `./scripts/gen-secrets.sh` (lo llena).
3. Agrega la variable al servicio `bot` en `hosts/pi/docker-compose.yml` y reinicia.

## Certificado
La web usa un certificado local de Caddy. Los clientes funcionan sin instalarlo (no
verifican), pero es mejor copiar la CA y ponerla en cada cliente (`ca` / `CA` /
«Certificado CA»):
```bash
docker compose -f hosts/pi/docker-compose.yml cp caddy:/data/caddy/pki/authorities/local/root.crt .
```

## Recordatorio
Windows (tarea cada 12 semanas), macOS (launchd, cada mes revisa 90 días), Android
(Termux:API semanal, con Wi-Fi y cargando) y Linux Guardian (temporizador de systemd). El
reporte diario del bot marca con ⚠ los dispositivos sin respaldo en más de 90 días.
