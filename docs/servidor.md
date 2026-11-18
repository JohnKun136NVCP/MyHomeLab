# PC Linux del homelab (cámaras y streaming)

La Pi vigila la red; la PC hace lo pesado: **Frigate** (graba y detecta personas/autos en tus
cámaras) y **Jellyfin** (tu Netflix casero). Todo se instala con un comando.

## Instalación automática
En la PC (Ubuntu/Debian, Fedora o Arch), con tu usuario normal:
```bash
git clone https://github.com/JohnKun136NVCP/MyHomeLab && cd MyHomeLab
./scripts/install-server.sh
```
Te pregunta 5 cosas (Enter = valor sugerido): IP de la Pi, puerto del dashboard, carpeta de
películas, carpeta de grabaciones (pon un disco grande) y la contraseña RTSP de tus Tapo.

| Paso | Qué hace |
|---|---|
| 1 | Instala Docker + Compose y te agrega al grupo `docker` |
| 2 | Detecta la GPU: **Intel** → Jellyfin transcodifica con QuickSync y Frigate detecta con OpenVINO; **AMD** → VA-API; **NVIDIA** → te avisa qué falta (va por CPU) |
| 3 | Crea el `.env` de la PC (ver `hosts/server/.env.example`) y `config/frigate/config.yml` |
| 4 | Levanta Frigate (`:5000`), Jellyfin (`:8096`) y node-exporter |
| 5 | UFW: servicios solo para la casa, métricas solo para la Pi, SSH solo desde el laboratorio |
| 6 | Entra a la Pi por SSH (con tu llave) y corre `./scripts/agregar-servidor.sh`: Grafana y alertas la monitorean, el dashboard usa su Frigate y aparecen sus enlaces en Inicio |
| 7 | Respaldo semanal cifrado (domingo 05:00) de su configuración → bot del homelab |

Puedes correrlo otra vez cuando quieras: no borra nada.

Opciones: `--pi 192.168.1.5 --puerto 8443 --pi-ssh usuario@192.168.1.5 --si` (sin preguntas).

**Antes:** ponle IP fija a la PC (reserva DHCP en el módem) y, si quieres que se registre sola,
copia tu llave a la Pi (`ssh-copy-id usuario@IP-de-la-Pi`).

## Después
- **Cámaras:** dashboard → Cámaras → «Detectar cámara genérica» (o ver [cámaras](camaras.md))
  y pega el bloque en `config/frigate/config.yml`; luego
  `docker compose -f hosts/server/docker-compose.yml --env-file .env restart frigate`.
  Las contraseñas de cada cámara van en el `.env` de la PC como `FRIGATE_<NOMBRE>_PASSWORD`.
- **Jellyfin:** abre `http://IP-de-la-PC:8096`, crea tu usuario y agrega la biblioteca `/media`.
  Si tienes GPU Intel/AMD: Panel → Reproducción → Transcodificación → «VA-API» o «Intel QuickSync»,
  dispositivo `/dev/dri/renderD128`.
- **Respaldo de tus archivos personales de la PC:** Linux Guardian (`clients/linux-guardian`),
  igual que en tu laptop.

## Qué se respalda de la PC
`.env`, `config/frigate` y la configuración de Jellyfin (usuarios, bibliotecas, progreso).
**No** las grabaciones ni las películas: son enormes y ya están en tus discos.
Restaurar: descifra con `HOMELAB_BACKUP_PASSWORD` del `.env` de la PC:
```bash
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in pc-….tar.gz.enc | tar xz
```
