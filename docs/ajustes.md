# Inicio y Ajustes (sin editar archivos a mano)

## Inicio de sesión
La web tiene su propia página para entrar (usuario y contraseña de Ajustes → Web) y un botón
**⎋ Salir** arriba a la derecha. La sesión dura 7 días en ese navegador. Tras 5 intentos
fallidos desde una IP se bloquea 10 minutos y te llega aviso al bot. Cambiar la contraseña
o el usuario en Ajustes no requiere reiniciar nada (cambiar el usuario cierra las sesiones).

Grafana y Vaultwarden siguen con su propio inicio de sesión.

## Inicio
La primera página de la web: todas las herramientas en un solo lugar (Grafana, Prometheus,
AdGuard, Uptime Kuma, Vaultwarden, módem, Tendas y tus enlaces) con un punto verde/rojo
según respondan. Clic y se abren.

## Ajustes (⚙)
Edita `.env` y `config/homelab.yml` desde el navegador:

- **Telegram, web, AdGuard, Vaultwarden, respaldos, Meta, Grafana**: los secretos nunca se
  muestran; deja el campo vacío para no cambiarlos. La contraseña de la web se guarda cifrada.
- **Servicios opcionales**: marca `dns`, `servicios` o `ids` y «Guardar y aplicar»: se
  encienden o apagan solos (se guarda en `COMPOSE_PROFILES` del `.env`, así que también
  `docker compose … up -d` a mano respeta lo que marcaste).
- **Equipos que respaldan**: agrega o quita equipos; cada nuevo recibe su token (se muestra
  una sola vez). ↻ genera otro si se filtró.
- **Editor avanzado** de `homelab.yml` para todo lo demás (redes, access points, cámaras,
  enlaces). Se valida antes de guardar.

Cada vez que guardas se deja una copia del archivo anterior en `run/` (las 10 últimas).

## Cómo se aplican los cambios
La web **no** tiene control de Docker (sería darle el control total de la Pi). Escribe
`run/apply-request` y el servicio `homelab-apply` del sistema corre `docker compose up -d`
con lo necesario y deja el resultado en `run/apply-result.json`, que la web te muestra.

Se instala una vez (ya lo hace `install-pi.sh`):
```bash
sudo ./scripts/install-apply-service.sh
```
Bitácora: `run/apply.log` · estado: `systemctl status homelab-apply.path`.
