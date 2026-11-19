# Reparto de servicios entre la Pi y tus PCs

La idea: la **Raspberry** hace lo ligero que debe estar siempre encendido (DNS, panel, bot,
VPN) y las **PCs o laptops viejas** hacen lo pesado (video, fotos, laboratorio). Si un equipo
se apaga o se descompone, lo demás sigue funcionando, y mover un servicio de una máquina a
otra es cambiar una línea.

Lo maneja **Ansible** desde la Pi: entra por SSH a cada máquina y corre los mismos scripts
del repo que correrías tú. Es la herramienta que se usa en las empresas para esto.

## Roles

| Rol | Qué lleva | Dónde conviene |
|---|---|---|
| `core` | Panel, bot, DNS (AdGuard), VPN, Prometheus/Grafana, Uptime Kuma, Vaultwarden | La Pi (gasta casi nada) |
| `media` | Jellyfin, Cine en casa, Frigate (cámaras) | Mini PC con gráfica Intel |
| `photos` | Immich | Donde esté el disco grande |
| `home` | Herramientas de la casa: Home Assistant, Paperless, Navidrome, Syncthing, Umami ([casa](casa.md)) | Cualquiera |
| `lab` | Wazuh, Greenbone, objetivos vulnerables ([laboratorio](laboratorio-seguridad.md)) | PC/laptop x86, encendida cuando estudias |
| `dns_backup` | Segundo AdGuard que copia al principal cada 10 min | Cualquier máquina que **no** sea la de `core` |

Una máquina puede tener varios roles. Solo una puede tener `core`.

## Cómo se usa

1. **Prepara cada máquina nueva**: instala Linux (Debian, Ubuntu o Fedora), conéctala **por
   cable** y deja entrar a la Pi por SSH con llave:
   ```bash
   # en la Pi
   ssh-copy-id usuario@IP-de-la-maquina          # agrega -p PUERTO si su SSH no es el 22
   ```
2. **Describe tus máquinas** en `config/homelab.yml` de la Pi:
   ```yaml
   machines:
     - name: pi
       host: 192.168.1.69
       user: pi
       local: true                 # la Pi es desde donde mandas
       roles: [core]
     - name: minipc
       host: 192.168.1.50
       user: john
       roles: [media, photos, home, dns_backup]
       home: [paperless, navidrome, syncthing]
     - name: laptop-vieja
       host: 192.168.1.60
       user: john
       ssh_port: 2222
       laptop: true
       battery_limit: 80
       roles: [lab]
       lab: [wazuh, greenbone]
   ```
3. **Revisa, prueba y aplica:**
   ```bash
   ./hl roles          # tabla: qué va en cada máquina (y si algo está mal escrito)
   ./hl roles ping     # ¿puede entrar a todas?
   ./hl roles check    # simula: qué cambiaría, sin tocar nada
   ./hl roles apply    # lo hace (pide tu contraseña de sudo una vez)
   ./hl roles apply minipc   # solo en una máquina
   ```
   También en el menú: `./hl` → 9 → Reparto.

La primera vez Ansible se instala solo en la Pi (`ansible-core`). En las demás máquinas no
hay que instalar nada aparte de SSH y Python, que ya traen.

## Qué hace en cada máquina

- **Todas**: paquetes básicos, Docker, y baja o actualiza el repo en `~/MyHomeLab` (sin pisar
  cambios tuyos).
- **Laptops** (`laptop: true`): no se duermen al cerrar la tapa, nunca suspenden y, si la
  batería lo permite, la carga se queda al `battery_limit` % (por defecto 80) para que no se
  infle. Usa adaptador USB‑Ethernet si no tiene puerto de red.
- **core**: `./hl update`. La primera instalación del núcleo sigue siendo a mano
  (`install-pi.sh` en la Pi o `./hl migrate` en una PC).
- **media**: `install-server.sh` la primera vez (la conecta con la Pi, Wake-on-LAN, firewall);
  después solo la mantiene arriba.
- **photos**: `immich.sh` y el botón «Fotos» en el panel de la Pi.
- **home**: enciende las herramientas de `home:` y pone sus botones en el panel de la Pi.
- **lab**: Wazuh, Greenbone y/o los objetivos, según `lab:`.
- **dns_backup**: un AdGuard con la misma configuración que el de la Pi. En el módem pon como
  DNS 1° la Pi y 2° esa máquina: si la Pi se reinicia, la casa sigue con internet.

## Mover un servicio

Cambia el rol de máquina en `homelab.yml` y corre `./hl roles apply`. Para Immich, que tiene
fotos y base de datos, usa la mudanza: `./scripts/immich.sh --pull-from usuario@IP` en la
máquina nueva ([fotos](fotos.md)). Ansible no borra nada de la máquina vieja: apágalo tú
cuando revises que todo funciona (`./hl home disable …`, `docker compose … down`).

## Qué equipo conseguir

- **Mini PC de oficina usada** (Lenovo Tiny, HP EliteDesk Mini, Dell OptiPlex Micro), Intel de
  7.ª gen o más nueva: poca luz, gráfica para video y caras, RAM ampliable. Ideal para `media`,
  `photos` y `home`.
- **Laptop vieja**: su batería es un no-break; ideal para `lab` o `home`.
- **Torre vieja**: gasta bastante luz; úsala para lo que prendes de vez en cuando (`lab`).

Más adelante, con más equipos, se puede agregar Proxmox (varias máquinas virtuales en una PC)
o un clúster pequeño de k3s para aprender Kubernetes; el reparto por roles sirve igual.
