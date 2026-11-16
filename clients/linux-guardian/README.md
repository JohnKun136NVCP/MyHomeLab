# 🛡 Linux Guardian

Respaldo con interfaz gráfica + "máquina del tiempo" para reinstalar tu Linux
(Debian / Ubuntu / Mint / Pop!_OS) tal como lo tenías.

## Instalar

```bash
tar xzf linux-guardian.tar.gz && cd linux-guardian
./install.sh           # apt: python3-venv, python3-tk, 7zip, curl, libnotify
                       # venv: Pillow, PyMuPDF y Telethon (requirements.txt)
linux-guardian         # o búscalo como "Linux Guardian" en el menú
```

Todo lo de Python se instala en un **entorno virtual** propio:
`~/.local/share/linux-guardian/venv`. No toca el Python del sistema ni tu `pip`.
Solo van por apt las cosas que un venv no puede contener (Tkinter, 7-Zip, curl, notify-send).

```bash
./install.sh --sin-telethon   # si solo vas a usar el bot
./install.sh --desinstalar    # borra programa, venv, menú y recordatorio
```

## Pestañas

| Pestaña | Qué hace |
|---|---|
| 🗄 **Respaldo** | Marcas sí/no por sección (paquetes, shell + p10k, Kitty, Git, VS Code, temas/fuentes, dconf, `~/.ssh`, `~/.gnupg`, contraseñas, rutas propias). Lo comprime en `.7z` con LZMA2 `-mx=9` y cifrado AES-256 (también se cifran los nombres de archivo). Opcional: instantánea de Timeshift y envío a Telegram. |
| 📁 **Explorador** | Navegas carpetas con vista previa de texto, imágenes, **PDF** (página por página, con su texto), carpetas (tamaño), ejecutables (bibliotecas que faltan con `ldd`, a qué paquete pertenecen) y `.deb` (qué dependencias faltan). «Agregar al respaldo» incluye lo que selecciones; los `.deb` se guardan aparte para que `restore.sh` los instale. |
| 📦 **Aplicaciones** | **Todas** las apps instaladas: las que tienen ventana (cada `.desktop` del sistema, del usuario, Flatpak y Snap) y los paquetes apt sin ventana. Buscador, filtro por tipo y casilla ☑/☐ por app (o selecciona varias y presiona Espacio). Las de apt/Flatpak/Snap se reinstalan por nombre; las **manuales** (AppImage, carpetas en `/opt` o en tu home) se copian completas. La selección se guarda sola. |
| 🎨 **Temas** | Temas GTK/Shell, íconos, **cursores**, **fuentes**, **temas SDDM**, temas y Look&Feel de Plasma, esquemas de color, decoraciones de ventana, Kvantum y fondos, tanto tuyos como del sistema. Marca ★ los que estás usando y muestra una vista previa. Lo que viene de un paquete se reinstala con apt; lo demás (por ejemplo un tema SDDM descargado en `/usr/share/sddm/themes`) se copia y se restaura con sudo. También guarda qué tema de SDDM tienes puesto. |
| 🧩 **Sistema** | Apps importantes (Zoom, Brave, Code, Telegram, OpenShot, Kitty, Zsh, Oh My Zsh, Powerlevel10k, Flatpak…) con su origen y versión; lista de apt/Flatpak/Snap/extensiones; temas; dependencias de cualquier paquete o `.deb`. |
| 🔑 **Contraseñas** | Tú las exportas (botones que abren el administrador de Brave o Firefox) y aquí eliges el CSV para meterlo cifrado. Incluye «Borrar de forma segura» (`shred`) para el CSV original. |
| ⏳ **Máquina del tiempo** | Extrae el respaldo y abre `restore.sh` en Kitty. También botones para Timeshift. |
| ✈ **Telegram** | Configuras el bot o tu cuenta, detectas tu `chat_id` y mandas un mensaje de prueba. |
| ⏰ **Recordatorio** | Temporizador de systemd de tu usuario: revisa a diario y, si pasaron 90 días desde el último respaldo, te manda una notificación de escritorio (y por Telegram). |

## Restaurar (`restore.sh`)

Viene dentro de cada respaldo. En el sistema nuevo:

```bash
7z x respaldo_*.7z            # o: 7z x respaldo_*.7z.001 si viene en partes
cd respaldo_*/ && ./restore.sh     # pregunta [S/n] en cada sección
./restore.sh --yes                 # todo sin preguntar
./restore.sh --dry-run             # solo muestra lo que haría
```

Orden: herramientas base → repos oficiales de Brave y VS Code → paquetes apt (salta los
que ya no existen) → **Zoom**: descarga el `.deb` más reciente, revisa cada dependencia con
`dpkg-deb -f Depends`, instala las que faltan y luego el programa → `.deb` respaldados →
Flatpak + Flathub → Snaps → configuraciones y `~/.ssh` (corrige permisos 700/600; lo que ya
exista se renombra a `*.antes-de-restaurar`) → zsh, Oh My Zsh, Powerlevel10k y fuente MesloLGS NF →
extensiones de VS Code → tema (dconf o gsettings).
Además: paquetes de los temas marcados, archivos fuera de tu home (`/usr/share/...`, `/opt/...`)
copiados con sudo, y tu tema de SDDM en `/etc/sddm.conf.d/`.

## Telegram: el límite de tamaño

La Bot API pública solo acepta **50 MB** por archivo, así que hay tres modos:

1. **Bot (API pública):** el respaldo se parte en volúmenes de 48 MB (`.7z.001`, `.002`…).
2. **Cuenta personal (recomendado para el límite de 2 GB):** Telethon ya viene en el venv;
   pon tu `api_id`/`api_hash` de <https://my.telegram.org>, presiona «Iniciar sesión» una vez
   y los archivos llegan a tus **Mensajes guardados**. Si pesa más de 2 GB se parte en volúmenes
   de ~1.9 GB, como pediste.
3. **Bot + servidor Bot API local** (`telegram-bot-api`): pones su URL en «Servidor API» y el
   límite sube a 2 GB.

Los volúmenes son el formato normal de 7-Zip: extrayendo el `.001` se unen solos.

## Notas de seguridad

- El token y la config viven en `~/.config/linux-guardian/config.json` (permisos 600). El token
  se le pasa a `curl` por stdin, así que no aparece en `ps`.
- La contraseña del `.7z` sí se le pasa a 7-Zip como argumento mientras comprime (así funciona
  `7z`), así que otro usuario de la misma máquina podría verla en ese momento. En una laptop
  personal no importa.
- El respaldo incluye tus llaves SSH: guárdalo con una contraseña fuerte.

## Enviar al homelab (MyHomeLab)

En la pestaña **Telegram → «Mi homelab (Raspberry)»** pon la URL de la Pi, el nombre del
dispositivo (`backups.devices` de `config/homelab.yml`) y su token (`BACKUP_TOKEN_…` del
`.env`). Presiona «Probar homelab» y marca «Enviar a mi homelab» en la pestaña Respaldo.
La Pi guarda cada respaldo en su carpeta, borra los viejos y te lo reenvía por el bot.
