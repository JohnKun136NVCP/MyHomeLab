# Honeypot (OpenCanary) en el laboratorio

Servicios **falsos** en la IP de la Pi en el laboratorio. Nadie legítimo debería
conectarse a ellos, así que cualquier intento es una alerta confiable: un equipo del lab
infectado escaneando, o alguien probando contraseñas.

| Puerto | Finge ser |
|---|---|
| 21 | FTP |
| 23 | Telnet |
| 2222 | SSH (tu SSH real del 19180 no se toca) |
| 1433 / 3306 | MSSQL / MySQL |
| 3389 | Escritorio remoto (RDP) |
| 5900 | VNC |
| 6379 | Redis |

## Seguridad
- Escucha **solo** en `HONEYPOT_IP` (la IP de la Pi en el lab, `wlan0`): no existe para la
  red de la familia ni para internet.
- Contenedor sin capacidades (solo puede abrir puertos), `no-new-privileges`, no reenvía
  nada: solo registra.
- Si algo no te gusta: `docker compose ... stop honeypot` y todo queda como antes.

## Activarlo
```bash
# .env:  HONEYPOT_IP=192.168.100.5
sudo ufw allow in on wlan0 to 192.168.100.5 port 21,23,1433,2222,3306,3389,5900,6379 proto tcp comment 'honeypot'
sudo docker compose -f hosts/pi/docker-compose.yml --env-file .env --profile honeypot up -d --build
```
Prueba desde un equipo del lab: `nc -v 192.168.100.5 2222` → en menos de 2 min llega
«🍯 Honeypot: … tocó SSH (señuelo)» al bot, y la IP aparece morada en web → Conexiones.
