# Laboratorio de seguridad (PC)

Dos partes que se usan juntas:

- **Wazuh** (SIEM): junta los registros de la Pi, el VPS y la PC, detecta ataques y te los
  muestra en un dashboard. Es lo mismo que se usa en un SOC.
- **Objetivos vulnerables**: DVWA, Juice Shop y (opcional) Metasploitable 2, en una red de
  Docker **sin salida** a internet ni a tu casa, más un contenedor Kali para atacarlos.

Todo vive en `lab/security/` y se maneja con un solo script:

```bash
./lab/security/lab.sh wazuh                    # SIEM (x86_64, 4 núcleos, ~8 GB RAM)
./lab/security/lab.sh targets [--metasploitable]
./lab/security/lab.sh attack                   # terminal de Kali dentro de la red aislada
./lab/security/lab.sh status | apagar | quitar
```

| Qué | Dónde |
|---|---|
| Dashboard de Wazuh | `https://127.0.0.1:5602` (solo desde la PC) |
| DVWA | `http://127.0.0.1:8181` (admin / password) |
| Juice Shop | `http://127.0.0.1:8182` |
| Red aislada | `10.250.66.0/24`: dvwa .10, juice-shop .11, metasploitable .12, atacante .100 |
| Tus archivos del atacante | `lab/security/trabajo/` (montado en `/root/trabajo`) |

## Reglas del laboratorio

- Las máquinas son vulnerables **a propósito**. No cambies `internal: true` de la red
  `objetivos` ni publiques sus puertos en la LAN.
- Ataca solo lo que está en `10.250.66.0/24` y tus propios equipos. Escanear redes o equipos
  ajenos (la escuela, el trabajo, vecinos) sin permiso por escrito es ilegal.
- Apaga los objetivos cuando no practiques: `./lab/security/lab.sh stop`.

## Wazuh

### Primera vez

1. `./lab/security/lab.sh wazuh`. Descarga `wazuh-docker` (versión fija), genera los
   certificados internos y levanta manager, indexer y dashboard. Tarda unos minutos.
2. Entra a `https://127.0.0.1:5602` con `admin` / `SecretPassword`.
3. **Cambia esa contraseña** antes de abrir el dashboard a la red. El procedimiento oficial
   (sección «Change the password of Wazuh users» de la documentación de wazuh-docker) es:
   generar el hash con la herramienta `hash.sh` del contenedor del indexer, ponerlo en
   `config/wazuh_indexer/internal_users.yml`, cambiar `INDEXER_PASSWORD` en
   `docker-compose.yml` y aplicar con `securityadmin.sh`. Todo dentro de
   `lab/security/wazuh-docker/single-node/`.
4. Si quieres verlo desde tu laptop del laboratorio, cambia en ese `docker-compose.yml`
   `127.0.0.1:5602:5601` por `5602:5601` y abre el puerto solo para esa IP:
   `sudo ufw allow from IP-laptop to any port 5602 proto tcp comment 'homelab wazuh dashboard'`.

### Agentes

En la Pi (y en el VPS, usando la IP de la PC dentro del túnel WireGuard):

```bash
sudo ./lab/security/wazuh-agent.sh 192.168.1.50
```

Instala el agente (misma versión que el manager, retenido con `apt-mark hold`) y además le
dice que lea el `eve.json` de Suricata del homelab y el log de UFW. En el dashboard aparecen
en **Agents**. Para quitarlo: `sudo ./lab/security/wazuh-agent.sh --remove`
(`clean-pi.sh` también lo quita).

## Greenbone (escáner de vulnerabilidades)

Revisa tus equipos en busca de fallas conocidas (versiones viejas, configuraciones débiles,
servicios expuestos), como lo haría una auditoría.

```bash
./lab/security/greenbone.sh install     # versión oficial; la 1ª vez baja sus bases (tarda horas)
./lab/security/greenbone.sh lan         # para verlo desde otra máquina de la casa
./lab/security/greenbone.sh stop|start|status|password|remove
```

Web: `http://127.0.0.1:9392` (usuario `admin`, contraseña en `lab/security/greenbone/admin.txt`).
Para tu primer escaneo: **Scans → Tasks → Task Wizard** con la IP de la Pi. Escanea solo tus
equipos y, si es la red de la casa, avisa: un escaneo completo hace lento algún equipo viejo.
Va en una PC x86 con ~8 GB de RAM (rol `lab: [greenbone]` en `./hl roles`).

## ntopng (tráfico de la red)

Muestra quién usa la red, con qué protocolos, a dónde se conecta cada equipo y cuánto
descarga. `./hl services enable ntopng` en la Pi → `http://IP-de-la-Pi:3005` (la primera vez
`admin`/`admin`). Mira la tarjeta de `NTOPNG_IFACE` en `.env` (por defecto `eth0`). Si su imagen
no arranca en la Pi, ponlo en una PC.

## Prácticas sugeridas

Cada práctica tiene un lado de ataque y un lado de detección: ataca desde Kali o desde tu
laptop del laboratorio y luego busca la huella en Wazuh, Suricata o el panel del homelab.

| # | Ataque | Dónde mirar |
|---|---|---|
| 1 | Escaneo: `nmap -sV 10.250.66.0/24` desde el atacante | Qué servicios y versiones expone cada objetivo; compáralos con CVE conocidas |
| 2 | Fuerza bruta de SSH a la Pi desde una laptop del lab (`hydra` con una lista corta) | Wazuh → reglas de autenticación fallida; el panel → «intentos bloqueados» de UFW; CrowdSec en el VPS |
| 3 | DVWA en nivel *low*: inyección SQL a mano y luego con `sqlmap` | Logs de Apache del contenedor; repite en *medium* y *high* y explica qué cambió en el código («View Source») |
| 4 | DVWA: XSS reflejado y almacenado | Por qué la cookie no tiene `HttpOnly`; compáralo con la cookie del panel del homelab (sí lo tiene) |
| 5 | Juice Shop: el «Score Board» y los retos de 1–2 estrellas | Cada reto enseña una categoría del OWASP Top 10 |
| 6 | Metasploitable: `vsftpd 2.3.4` y `distcc` con Metasploit | Lee el módulo antes de lanzarlo: qué falla y por qué |
| 7 | Integridad: cambia un archivo de `/etc` en la Pi | Wazuh → File Integrity Monitoring (syscheck) |
| 8 | Tráfico raro: `curl` a un dominio de prueba de malware desde la red de casa | AdGuard lo bloquea y el panel avisa; Suricata lo marca en `eve.json` → Wazuh |

Toma notas de cada práctica (comando, qué esperabas, qué viste, cómo se detectaría y cómo se
evita). Es justo el formato de un reporte de pentest y de un ticket de SOC.

## Recursos

- [Wazuh documentation](https://documentation.wazuh.com/current/) — reglas, decoders, FIM, active response.
- [OWASP Top 10](https://owasp.org/www-project-top-ten/) y la guía de pruebas WSTG.
- [DVWA](https://github.com/digininja/DVWA) y [OWASP Juice Shop](https://owasp.org/www-project-juice-shop/).
