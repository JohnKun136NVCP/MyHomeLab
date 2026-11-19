# Uptime Kuma y Vaultwarden (perfil «servicios»)

```bash
sudo docker compose -f hosts/pi/docker-compose.yml --env-file .env --profile servicios up -d
```

## Uptime Kuma — `http://IP-de-la-Pi:3001`
Usa la versión 2 (`louislam/uptime-kuma:2-slim`). Si ya tenías la 1.23, actualiza así
(respalda primero: la migración convierte la base de datos):
```bash
sudo docker compose -f hosts/pi/docker-compose.yml --env-file .env stop uptime-kuma
sudo docker run --rm -v homelab-pi_uptime_kuma:/d -v "$PWD":/b alpine tar czf /b/uptime-kuma-v1.tgz -C /d .
git pull && sudo docker compose -f hosts/pi/docker-compose.yml --env-file .env up -d uptime-kuma
sudo docker compose -f hosts/pi/docker-compose.yml --env-file .env logs -f uptime-kuma   # ver la migración
```

Monitorea páginas y servicios con historial: tu página de empresa, el VPS, el nginx del 8090,
la Bot API, etc. Los avisos llegan por el bot del homelab con un **Webhook**:
URL `http://bot:8080/notify`, cuerpo `application/json` y encabezado
`{"Authorization": "Bearer TU_INTERNAL_TOKEN"}` (`bot` es el nombre del contenedor del bot
dentro de la red de Docker; desde fuera de la Pi ese puerto no existe, a propósito).

> **Monitor DNS en rojo aunque AdGuard funciona:** Uptime Kuma pregunta desde la red de Docker
> (172.x) y UFW lo bloquea. `./hl firewall` agrega la regla (o
> `sudo ufw allow from 172.16.0.0/12 to any port 53`).

### Pestaña «Servicios» del dashboard y resumen diario
1. En Uptime Kuma: **Páginas de estado → Nueva página de estado**, slug `homelab`.
2. Agrega tus monitores (puedes agruparlos: «Pi», «VPS», «Casa») y guarda.

El dashboard muestra cada servicio con sus últimos latidos y su % arriba en 24 h, Inicio
muestra «X/Y arriba» y el **resumen diario** del bot incluye los caídos o inestables.
El slug se cambia en Ajustes → Avisos.

### Avisos: resumen diario y horario de silencio (Ajustes → Avisos)
- **Hora del resumen diario**: equipos nuevos, servicios caídos, DNS bloqueado, VPS,
  CrowdSec, cámaras y respaldos en un solo mensaje.
- **Horario de silencio** (ej. `23:00-07:00`): lo urgente (un servicio caído, alerta
  crítica, IDS crítico) suena siempre; lo demás llega **sin sonido**, y los eventos no
  urgentes de la red esperan a que termine el horario.

## Vaultwarden (gestor de contraseñas) — `https://IP-de-la-Pi:8443/vault/`
Compatible con las apps y extensiones de **Bitwarden** (en la app: «Autohospedado» y esa URL).
1. Entra y **crea tu cuenta**.
2. Ajustes → Vaultwarden → desmarca **Permitir crear cuentas** → Guardar y aplicar.
3. Panel de administración: `/vault/admin` (solo administradores del dashboard + `VW_ADMIN_TOKEN`).
4. Desde fuera de casa: solo por la VPN (no abras puertos del módem).

### Cuentas para la familia (cada quien su bóveda)
Con los registros cerrados, invita a cada persona:
1. `/vault/admin` → **Users → Invite User** → su correo.
2. Esa persona abre `/vault`, elige **Crear cuenta** con **ese mismo correo** y su propia
   contraseña maestra (sin servidor de correo no llega email: basta con registrarse).
3. Para compartir contraseñas (WiFi, streaming, banco familiar): en tu bóveda crea una
   **Organización** «Casa» e invítalos; lo que pongas en sus colecciones lo ven todos.

Nadie (ni tú como admin) puede ver la bóveda personal de otro: está cifrada con su contraseña
maestra. Si la olvidan, no se recupera: que la guarden en papel.

**Respaldo (importante):** tus contraseñas viven en `VW_DATA` (ponlo en tu SSD). Entra en el
[respaldo semanal del homelab](respaldos.md#respaldo-del-homelab) y aun así guarda aparte el
«Exportar bóveda» cifrado de la app de vez en cuando. Si pierdes eso, pierdes las contraseñas.
Las apps de Bitwarden guardan una copia local, así que si la Pi se apaga sigues viéndolas.

## Speedtest Tracker — `http://IP-de-la-Pi:8765`

Mide la velocidad del internet cada 6 horas (perfil `speedtest`) y guarda el historial: sirve
para reclamarle al proveedor con datos. La Pi va por cable al módem, así que mide lo real.
La primera vez crea tu usuario en la web; en **Ajustes → Notificaciones** puedes poner un
aviso por Telegram si baja de cierta velocidad. Cambia la frecuencia con `SPEEDTEST_SCHEDULE`.

## Diun — avisos de imágenes nuevas

Perfil `diun`. Cada día revisa si hay versión nueva de las imágenes de Docker que usas y te
manda un mensaje sin sonido. No actualiza nada solo: tú decides con `./hl update`
(o `./hl pull && ./hl up -d`). Antes de actualizar Immich o Authelia lee sus notas de versión.

## Encender la PC a distancia (Wake-on-LAN)

`install-server.sh` registra la MAC de la PC y deja la tarjeta lista para despertar. Luego:
panel → **Inicio** → «Encender equipos», o en Telegram `/encender`. Para otros equipos
agrégalos en `homelab.yml`:

```yaml
wake_on_lan:
  - name: PC
    mac: aa:bb:cc:dd:ee:ff
```

Activa «Wake on LAN» / «Power on by PCI-E» en el BIOS. Solo funciona por cable.

## Prueba mensual del respaldo

`sudo ./hl test-backup --install` programa una prueba el día 1 de cada mes: descifra el
respaldo más reciente del homelab en una carpeta temporal, revisa que trae `.env`,
`homelab.yml` y los datos, que las bases SQLite estén íntegras y que no tenga más de 9 días.
Te llega ✅ (sin sonido) o ⚠/❌ con lo que falló. `sudo ./hl test-backup` la corre ya.
