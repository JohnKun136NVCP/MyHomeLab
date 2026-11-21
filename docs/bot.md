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


## Cuentas de la casa en el bot
Solo `TELEGRAM_ALLOWED_IDS` (tú) puede usar todos los comandos. Las demás personas se vinculan
desde el dashboard (Mis respaldos → «Vincular mi Telegram», o `/vincular 123456`):

| Rol de la cuenta | Qué puede en el bot |
|---|---|
| Familia | `/misrespaldos`, `/desvincular`, mandar fotos/archivos (se guardan en **su** respaldo) y recibir los avisos de **sus** respaldos |
| Ve registros | lo anterior + alertas, reporte diario, fotos de cámaras y `/estado /reporte /vps /dispositivos /nuevos /eventos /conexiones /dns /camaras /foto` |

Nadie más que tú puede bloquear, programar publicaciones ni cambiar nada. Un chat que no está
vinculado sigue sin recibir respuesta (y a ti te llega «🚫 Alguien intentó usar tu bot»).

## Recordatorios

Tú y cada cuenta vinculada de la casa:

```
/recordar mañana 9:00 pagar la luz
/recordar 15/12 18:00 cita con el dentista      (sin hora: 9:00)
/recordar en 2h sacar la ropa                    (m, min, h, d)
/recordar cada lunes 7:30 sacar la basura
/recordar cada mes 5 pagar el internet           (31 = último día del mes)
/recordar cada año 15/03 cumpleaños de mamá
/recordatorios                                   los tuyos, con su número
/olvidar 3                                       borra el #3
```

Cada chat ve y borra solo los suyos. La hora es la de `social.timezone` en `homelab.yml`
(por defecto Ciudad de México). Se guardan en la base del bot, así que van en el respaldo.

## Tus otros bots (panel → Bots)

Si en la Pi corren otros bots tuyos —como
[yt-telegrambot](https://github.com/JohnKun136NVCP/yt-telegrambot), que comparte el servidor
`telegram-bot-api` local— el panel los administra sin cambiarles nada:

```bash
sudo ./hl bots add yt ~/yt-telegrambot     # detecta el tipo y su servicio (yt-telegrambot.service)
./hl bots list
sudo ./hl bots restart yt                 # también start, stop
sudo ./hl bots logs yt -f
sudo ./hl bots cli yt grant 123456789 premium --days 30   # su propio database/dbcli.py
sudo ./hl bots remove yt                  # deja de administrarlo (el bot sigue igual)
```

En el panel (solo administrador):
- **Estado** del servicio de systemd (corriendo, desde cuándo, reinicios, memoria), su
  @usuario, y botones de reiniciar, apagar y encender, además de los **logs** (el token se tapa).
- **yt-telegrambot**: usuarios, activos hoy, premium, pagos, Stars, canciones en caché y cola.
  - **Usuarios**: buscar por ID o @, dar premium 30 días / 1 año / N días / sin vencer, hacer
    admin, regresar a gratis, poner su cuota del día en 0, mandarle un mensaje o borrarlo.
    A los de `ADMIN_IDS` del `.env` del bot no se les quita nada desde aquí.
  - **Pagos**: la lista y **Reembolsar** (igual que su `/refund`: Telegram regresa las
    Stars, se marca el pago y se le quita el premium).
  - **Canciones**: la caché del bot (título, canal, duración, formato y MB), con búsqueda y
    orden. Borrar una (o varias seleccionadas) la quita de `idSongs.db`, borra su audio de
    `Songs/` y su miniatura; si alguien la vuelve a pedir, el bot la baja de nuevo. Las que se
    están enviando en ese momento no se tocan. También lista y borra los **archivos sin
    registro** (audios en `Songs/` que la base no conoce).
  - **Mensaje** a todos los usuarios (mantenimiento, novedades).
- Si el bot deja de contestar (se juntan mensajes sin leer por 15 min) te llega un aviso
  «🤖 Bot … no está contestando».

Cómo funciona: la carpeta del bot se monta (bind en `/etc/fstab`) en `/srv/homelab-bots/NOMBRE`
y solo esa carpeta entra al panel. Los cambios usan el propio `database/databases.py` del bot
(mismas reglas, sin copias) y los archivos de SQLite siguen siendo de su usuario. Con su
`TOKEN` solo se llaman `getMe`, `getWebhookInfo`, `sendMessage` y `refundStarPayment`: nunca
`getUpdates`, así que no le roba mensajes. Encender, apagar y logs los hace `homelab-apply`
como root (el panel no toca systemd). Tipo `generic` para cualquier otro bot: estado,
reinicio, logs y @usuario.
