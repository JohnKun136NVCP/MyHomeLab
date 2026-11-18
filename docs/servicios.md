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
