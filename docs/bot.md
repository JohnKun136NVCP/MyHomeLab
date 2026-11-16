# Bot de Telegram

## Servidor Bot API local (2 GB)
El bot usa tu `telegram-bot-api` de la Raspberry (`TELEGRAM_API_URL`). Con `--local`:
- sube archivos de hasta **2000 MB** (el bot usa partes de 1900 MiB para no pasarse);
- `getFile` regresa la ruta del archivo en disco: monta esa carpeta en el contenedor con la
  **misma ruta** (`TELEGRAM_API_DATA`) para que el bot copie lo que le mandas.

Si alguna vez le hiciste `logOut` al bot para usar tu servidor, **no** apuntes a
`api.telegram.org`.

## Mismo token, ¿hay problema?
No para enviar: cualquier programa puede mandar mensajes y archivos con el mismo token
(Linux Guardian, este bot, scripts). Lo único que no se puede compartir es **leer** las
actualizaciones: solo un programa a la vez puede usar `getUpdates` (o un webhook). Si tu
bot actual ya las lee, este chocará con él. Opciones:
- deja que este bot sea el único que las lee (recomendado: ya incluye los comandos), o
- crea un segundo bot con @BotFather para el homelab y pon `BOT_NO_POLL=1` en el que solo envía.

## Seguridad
- Solo `TELEGRAM_ALLOWED_IDS` puede usarlo. El primer ID recibe alertas y reportes.
- Si alguien más le escribe, se le ignora y te llega «Alguien intentó usar tu bot» (una vez
  al día por persona).
- El token nunca aparece en la línea de comandos (`curl -K -` por stdin) ni en los logs.
- La subida de respaldos usa un token **por dispositivo**; si uno se filtra lo cambias sin
  tocar los demás.

## Alertas que llegan
| Origen | Ejemplos |
|---|---|
| netwatch | dispositivo nuevo, posible ARP spoofing, puertos nuevos, IDS, bloqueado conectado |
| Prometheus | nodo caído (Pi/PC/VPS), sin internet, AP caído, CPU/RAM/disco/temperatura, tráfico anómalo |
| Frigate | persona/auto detectado, con foto |
| Respaldos | recibido, rechazado (token inválido), limpieza de viejos |

Los avisos no urgentes esperan al final de `anomalies.quiet_hours`; los críticos llegan siempre.
El reporte diario sale a `reports.daily_time`.
