# 🍿 Cine en casa (streaming en la PC)

Un portal propio para tus películas y series, con perfiles de la familia, «Seguir viendo»,
«Siguiente episodio», «Mi lista», búsqueda y su propio reproductor. Por dentro usa **Jellyfin**
(que organiza la biblioteca, descarga portadas y convierte el video cuando hace falta).

| Dónde | Dirección |
|---|---|
| Tele, computadora o celular | `http://IP-de-la-PC:8097` |
| Tele (modo control remoto) | `http://IP-de-la-PC:8097/?tele` (lo recuerda la siguiente vez) |
| Administrar Jellyfin (usuarios, bibliotecas) | `http://IP-de-la-PC:8096` |

Lo instala `./scripts/install-server.sh` (servicio `cine` en `hosts/server/docker-compose.yml`).

## Primera vez: preparar Jellyfin (10 minutos)
1. Abre `http://IP-de-la-PC:8096` y sigue el asistente: idioma **Español**, crea tu usuario administrador.
2. **Bibliotecas**: agrega una de tipo **Películas** y otra de **Series**, las dos dentro de `/media`
   (es tu carpeta `MEDIA_DIR`). Ordena así para que reconozca todo:
   ```
   Películas/El último verano (2019)/El último verano (2019).mkv
   Series/Los Gutiérrez/Temporada 01/Los Gutiérrez S01E01.mkv
   ```
3. **Perfiles de la familia**: Panel → Usuarios → «+». A cada uno:
   - activa **Mostrar el usuario en la pantalla de inicio de sesión** (así sale en «¿Quién va a ver?»),
   - contraseña opcional (los niños pueden ir sin contraseña),
   - en **Control parental**, la clasificación máxima para los niños,
   - en su perfil puede subir una foto (sale en vez de la inicial).
4. Con GPU Intel/AMD: Panel → Reproducción → Transcodificación → VA-API o Intel QuickSync,
   dispositivo `/dev/dri/renderD128` (el instalador ya conectó la tarjeta al contenedor).

## Cómo se usa
- **Tele**: abre el navegador de la tele (o una Fire TV / Chromecast con navegador) en
  `http://IP-de-la-PC:8097/?tele`. Se maneja con **flechas, OK y Atrás**: el elemento elegido
  tiene un aro dorado. En el reproductor: ← → adelanta/regresa 10 s, OK pausa, ↑ ↓ muestran los controles.
  Los botones ⏪ ⏩ ⏯ del control también funcionan.
- **Computadora**: igual que Netflix; teclas: espacio (pausa), ← → (10 s), F (pantalla completa).
- **Celular**: abre la dirección y en el menú del navegador elige **Agregar a la pantalla de
  inicio**: queda como una app.

Lo que veas se guarda en Jellyfin: si dejas una película a la mitad en la tele, en el celular
aparece en **Seguir viendo** en el mismo minuto. Al terminar un episodio, sigue el siguiente solo.

## Reproducción
- Si el equipo puede reproducir el archivo tal cual (MP4/MKV con H.264, VP9, AV1…), va **directo**:
  sin gastar CPU de la PC y con la mejor calidad.
- Si no (HEVC en un navegador que no lo soporta, audio DTS, subtítulos de imagen…), Jellyfin lo
  **convierte al vuelo** (HLS). Es automático.
- Subtítulos e idioma: botones 💬 y ♫ del reproductor. Los subtítulos de imagen (PGS de Blu-ray) se
  «queman» en el video.
- Calidad máxima al convertir: `maxBitrate` en `services/cine/config.js` (20 Mbps por defecto).

## Personalizar
`services/cine/config.js`: nombre del portal (`title`), dirección de Jellyfin y calidad. No hay que
reconstruir nada: recarga la página.

Las letras (Big Shoulders Display y Figtree) se guardan en la PC con
`./services/cine/descargar-letras.sh` (lo hace el instalador) para que se vean igual sin internet.

## Compatibilidad
Funciona con Jellyfin **10.9 o más nuevo, incluido 12.0**: usa la autenticación actual
(encabezado `Authorization` y `ApiKey`), no la antigua (`api_key`, `X-Emby-Authorization`), que
Jellyfin 12 ya desactivó.

## Si algo falla
| Ves | Qué hacer |
|---|---|
| «No encuentro el servidor de películas» | `docker compose -f hosts/server/docker-compose.yml --env-file .env ps` en la PC: `jellyfin` y `cine` deben estar arriba |
| «¿Quién va a ver?» vacío | En Jellyfin activa «Mostrar el usuario en la pantalla de inicio de sesión», o usa «Otro usuario» |
| Un video no arranca en la tele | Prueba en la computadora; si ahí sí, es el códec: Jellyfin lo convertirá (revisa la aceleración por GPU) |
| Va a saltos | Baja `maxBitrate` en `config.js` o conecta la tele por cable |
