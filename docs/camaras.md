# Cámaras (Tapo, Imou, EZVIZ y genéricas tipo foco 360°) con Frigate

Frigate corre en la PC Linux (`hosts/server`, instalada con `./scripts/install-server.sh`), graba, detecta personas/autos y da la vista
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

- **Genérica «Cámara Foco WiFi 360 Seguridad»** (y mini cámaras sin marca): no tienen marca,
  lo que manda es **la app con que la configuraste**. Ve abajo.

## Cámaras genéricas (foco 360°)

1. Configúrala en su app y conéctala a tu WiFi. Ponle **IP fija** (reserva DHCP en el módem).
2. Dashboard → **Cámaras → Detectar cámara genérica**: escribe la IP, usuario (normalmente `admin`)
   y la contraseña del **dispositivo** (no la de tu cuenta de la app). El homelab prueba las rutas
   de cada familia, te dice cuál es y te da el bloque listo para pegar en `config/frigate/config.yml`.

| App con la que la configuras | RTSP | Ruta HD / SD | Qué hacer en la app |
|---|---|---|---|
| **iCSee / XMEye** | sí (554; abre 8899 y 34567) | `/user=admin&password=CLAVE&channel=1&stream=0.sdp?real_stream` / `stream=1` | Nada; de fábrica la contraseña va vacía, ponle una |
| **V380 / V380 Pro** | sí, hay que activarlo | `/live/ch00_0` / `/live/ch00_1` | ⚙ → RTSP/ONVIF → activar y poner contraseña |
| **Yoosee** | sí | `/onvif1` / `/onvif2` | Usuario `admin` + «contraseña del dispositivo» |
| **CamHi** | sí | `/11` / `/12` | Usuario y contraseña de la cámara |
| **Tuya / Smart Life / CloudEdge / YI IoT** | casi nunca | — | Solo funcionan con su nube. Si ves «ONVIF» en ajustes avanzados, actívalo |

Si la tuya es Tuya/Smart Life sin ONVIF, no hay forma limpia de meterla a Frigate: te sirve más
cambiarla por una iCSee, V380 o Tapo (las tres cuestan parecido).

Consejos con focos 360°:
- Casi todos son **solo WiFi 2.4 GHz**: ponlos en la red de la casa, no en el laboratorio.
- Bloquéales internet si no usas su app (dashboard → ACL, en modo puente) — muchas mandan video a
  servidores en China. Con RTSP + Frigate no necesitan salir a internet.
- Usa la ruta SD para `detect` y la HD para `record` (el bloque que genera el dashboard ya lo hace).

## IP fija

Pon a cada cámara una **IP fija** (reserva DHCP en el módem) y agrégala en
`config/frigate/config.yml` (se crea desde `config.example.yml`, que trae un ejemplo de cada marca).

## Probar
```bash
ffplay rtsp://USUARIO:CLAVE@192.168.0.20:554/stream2     # o VLC → Abrir ubicación de red
```

## Rendimiento
- Detección con la resolución SD (`detect`) y grabación con la HD (`record`), ya configurado.
- PC Intel con gráficos integrados: descomenta `/dev/dri` en `hosts/server/docker-compose.yml`
  y usa el detector OpenVINO (comentado en el config).
- En la Pi 4 sola: 1–2 cámaras, `fps: 5` y sin grabación continua.
