# Cámaras (Tapo, Imou, EZVIZ) con Frigate

Frigate corre en la PC Linux (`hosts/server`), graba, detecta personas/autos y da la vista
en vivo (go2rtc). La web del homelab muestra la foto actual de cada cámara y el bot te
manda foto cuando detecta algo (`cameras.notify_labels`).

## Activar RTSP
- **TP-Link Tapo**: app Tapo → cámara → ⚙ → Configuración avanzada → **Cuenta de cámara** →
  crea usuario y contraseña. Ponlos en `.env` (`FRIGATE_RTSP_USER`, `FRIGATE_RTSP_PASSWORD`).
  Rutas: `rtsp://USUARIO:CLAVE@IP:554/stream1` (HD) y `/stream2` (SD).
- **Imou**: usuario `admin` y la contraseña/código de seguridad de la cámara.
  `rtsp://admin:CLAVE@IP:554/cam/realmonitor?channel=1&subtype=0` (subtype=1 = SD).
- **EZVIZ**: usuario `admin` y el código de verificación de 6 letras de la etiqueta; en la
  app activa «Protocolo local / RTSP» si tu modelo lo pide.
  `rtsp://admin:CODIGO@IP:554/h264/ch1/main/av_stream`.

Pon a cada cámara una **IP fija** (reserva DHCP en el módem) y agrégala en
`config/frigate/config.yml` (hay un ejemplo de cada marca).

## Probar
```bash
ffplay rtsp://USUARIO:CLAVE@192.168.0.20:554/stream2     # o VLC → Abrir ubicación de red
```

## Rendimiento
- Detección con la resolución SD (`detect`) y grabación con la HD (`record`), ya configurado.
- PC Intel con gráficos integrados: descomenta `/dev/dri` en `hosts/server/docker-compose.yml`
  y usa el detector OpenVINO (comentado en el config).
- En la Pi 4 sola: 1–2 cámaras, `fps: 5` y sin grabación continua.
