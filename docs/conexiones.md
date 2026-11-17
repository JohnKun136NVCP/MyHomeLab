# Conexiones entrantes (web → Conexiones)

Muestra **quién se conecta a la Raspberry**: desde qué red (casa, laboratorio, oficinas por
el NAT del Tenda, VPN o Internet), a qué servicio y si se permitió o se bloqueó. Clic en una IP para ver su historial, eventos y el comando para bloquearla en UFW.

| Fuente | Qué ve | Requisito |
|---|---|---|
| conntrack (`/proc/net/nf_conntrack`) | conexiones **permitidas**, incluidas las que Docker redirige (web, Grafana…) | nada (Docker ya carga el módulo) |
| Log de UFW (`/var/log/ufw.log`) | intentos **bloqueados** por el firewall | `sudo ufw logging low` |

Se ignoran DNS, DHCP, mDNS, NTP y similares (ruido). Se guardan 14 días.

**Límite:** sin modo puente solo se ven las conexiones **a la Pi**, no las de otros equipos
entre sí ni hacia internet. Para «IPs entrantes» de toda la casa haría falta el modo puente.

Bot: `/conexiones` resume las últimas 24 h.

# Buscar un equipo que no aparece (web → Dispositivos → 🔎 Buscar en la red)
Escribe una IP, MAC o nombre en el filtro y presiona el botón. netwatch:
1. busca en la lista (IP, MAC, nombre, hostname, fabricante);
2. si es un nombre, le pregunta su IP al DNS del módem y de los Tenda;
3. si es una IP desconocida: ping, ARP, nombre y puertos; si responde, la **agrega** a la lista.

Los **hostnames** salen, en orden, de AdGuard, del DNS del módem/Tenda (los nombres que dio
su DHCP), de mDNS (iPhone, Mac, Linux, impresoras) y de NetBIOS (Windows). La columna
«Hostname» dice de dónde salió cada uno.
