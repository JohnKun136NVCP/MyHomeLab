# Respaldos para toda la casa (sin saber de computadoras)

Cada persona tiene su cuenta en el dashboard (el admin la crea en **Cuentas**) y su página
**📦 Mis respaldos**. Nadie ve los respaldos ni los avisos de los demás.

## Para quien NO es técnico: 3 formas

| Equipo | Qué hacer | ¿Se repite solo? |
|---|---|---|
| **Celular** (Android o iPhone) | Abre el dashboard en el navegador → Mis respaldos → **📷 Elegir fotos y archivos** (o «Elegir una carpeta») | No: cuando quieras |
| **Computadora Windows** | Mis respaldos → **🪟 Windows** → descarga el archivo `.cmd` en esa computadora → doble clic | Sí, cada 4 semanas |
| **Linux o Mac** | Mis respaldos → **🐧 Linux / 🍎 Mac** → copia el comando → pégalo en la Terminal | Sí, cada 4 semanas |
| Con **Telegram** | Vincula tu Telegram y mándale fotos o archivos al bot | No: cuando quieras |

### Windows, paso a paso
1. En esa computadora entra al dashboard (`https://IP-de-la-Pi:8443`) con tu usuario.
2. Mis respaldos → escribe un nombre («laptop de mamá») → **Windows**.
3. Haz clic en **Respaldo-….cmd** y ábrelo. Si sale «Windows protegió su PC»:
   *Más información → Ejecutar de todas formas* (es normal en archivos descargados).
4. Se abre una ventana, instala 7-Zip si falta y hace el primer respaldo. Al terminar, presiona una tecla.

Queda un acceso **«Respaldar ahora»** en el Escritorio y una tarea que respalda sola cada
4 semanas. Respalda **Documentos, Escritorio e Imágenes** (aunque estén en OneDrive).

### Linux o Mac
Mis respaldos → **Linux** o **Mac** → copia la línea que empieza con `curl` → abre la
**Terminal** → pégala → Enter. Hace el primer respaldo y deja un temporizador que revisa cada
día si ya pasaron 4 semanas (si la computadora estaba apagada, respalda en cuanto la prendas).
En Linux queda «Respaldar ahora (MyHomeLab)» en el menú.

### Celular
No hay que instalar nada. Desde el navegador del celular: Mis respaldos → **📷 Elegir fotos y
archivos**, selecciona todo lo que quieras (la galería permite elegir muchas a la vez) y espera
la barra. Te llega un solo aviso al final, aunque sean 300 fotos.

> ¿Respaldo automático del celular? Hoy la opción sin configurar es esta (manual). Para algo
> automático en Android se puede usar `clients/android/respaldo-termux.sh` (Termux), pero ya
> requiere instalar apps y dar permisos: mejor que lo configure el admin.

## Avisos: solo a cada quien
- Cuando llega un respaldo, el aviso aparece en **Mis respaldos → Avisos** (el número rojo del menú).
- Si la persona **vincula su Telegram** (Mis respaldos → «Vincular mi Telegram» → «Abrir
  Telegram» → Iniciar), el aviso y el archivo le llegan ahí. A nadie más.
- Al administrador solo le llega una línea («📦 Respaldo de mamá recibido, 1.2 GB»), **sin el archivo**.

## Restaurar
- **Mis respaldos → tu equipo → Archivos → ⬇** descarga cualquier respaldo tuyo.
- Los de computadora van cifrados. La **contraseña para restaurar** aparece en la tarjeta del
  equipo («ver»). Windows: ábrelo con 7-Zip y esa contraseña. Linux/Mac:
  `openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in ARCHIVO | tar xz`
- Lo que se sube desde el celular o Telegram se guarda tal cual (sin cifrar) en la Pi.
- Se borran solos después de `backups.max_age_days` (14 días): descarga lo que quieras conservar.

## Quién ve qué (lo decide el admin en Cuentas)
| Rol | Ve | Recibe por Telegram |
|---|---|---|
| **Familia** | Mis respaldos, Cámaras, Servicios, Accesos | solo avisos de **sus** respaldos |
| **Ve registros** | además Resumen, Dispositivos, Tráfico, Conexiones, Mapa y Eventos (sin cambiar nada) | sus respaldos + alertas y reporte diario del homelab; en el bot: /estado, /eventos, /dispositivos… |
| **Administrador** | todo | todo |

Las alertas del bot solo llegan a `TELEGRAM_ALLOWED_IDS` (tú) y a las cuentas con rol
**Ve registros** o **Administrador** que vincularon su Telegram.

## Que te llegue por Telegram y se borre de la Pi

En **Mis respaldos → «Cuando llega un respaldo»** cada quien elige (viene así por defecto):

- **Mandármelo por Telegram** (si vinculó su Telegram): llega cada respaldo; si pesa más de 2 GB,
  en partes `.001`, `.002`… que se juntan abriendo la `.001` con 7-Zip (Windows) o ZArchiver
  (Android). Las subidas desde el navegador llegan una por una o, si son más de 10, en un `.zip`.
- **Borrarlo de la Pi cuando ya lo tenga**: en cuanto llega a Telegram (o, sin Telegram, cuando
  lo descarga completo con ⬇), se borra de la Pi para ahorrar espacio. También hay 🗑 por archivo.

Ojo: con las dos opciones, **la copia queda en Telegram**: que no borren ese chat. Para el admin
principal el borrado viene apagado.
