# Mudar el homelab a la PC (y dejar la Pi limpia)

Dos scripts para cuando quieras que todo viva en tu PC Linux (o en un servidor más potente).

## `./hl migrar` — instalar todo en la PC

Córrelo **en la PC nueva**, dentro del repo clonado:

```bash
git clone https://github.com/JohnKun136NVCP/MyHomeLab && cd MyHomeLab
./scripts/migrar.sh --desde-pi pi@192.168.1.5     # copia tu configuración de la Pi
./scripts/migrar.sh --desde-pi pi@192.168.1.5:2222   # si el SSH de la Pi está en otro puerto
./scripts/migrar.sh --desde-cero                  # instalación nueva
```

También sirve un alias de `~/.ssh/config` (`--desde-pi mipi`) o `--puerto-ssh 2222`. Si el
puerto no contesta, te lo pregunta. Usa una sola conexión SSH para todo el proceso.

Opciones: `--con-respaldos` (trae también los respaldos de los equipos), `--con-tunel` (mueve
el WireGuard de la Pi a la PC), `--sin-pc` (no instala Frigate/Jellyfin/Immich), `--limpiar-pi`
(al final deja la Pi limpia).

Qué hace:

1. Instala Docker y lo necesario (apt, dnf o pacman).
2. **Desde la Pi**: crea una llave SSH si no tienes y la copia a la Pi (te pide la contraseña
   una sola vez), hace un respaldo cifrado en la Pi, lo trae y lo restaura aquí.
3. Ajusta lo que cambia de máquina: IP, interfaces de red (te pregunta cuál es la de la casa y
   cuál la del laboratorio), el servidor Bot API local (lo corre en un contenedor; te pide
   `api_id`/`api_hash`) y los puertos 80/443 si están libres.
4. Levanta todo junto en la PC (el panel, el bot y además Frigate, Jellyfin, Cine en casa e
   Immich), el firewall y los servicios programados (respaldos, aplicar cambios).
   Si la Pi ya tenía **Immich**, te ofrece mudarlo con todas sus fotos, cuentas y álbumes
   (o dejarlo en la Pi y que la PC solo haga el reconocimiento de caras). Ver [fotos](fotos.md).
5. **SSH y firewall de esta máquina**:
   - Si la Pi tiene SSH en otro puerto (p. ej. 2222), te ofrece poner el mismo aquí
     (`/etc/ssh/sshd_config.d/05-homelab-puerto.conf`; en Ubuntu reciente reinicia `ssh.socket`;
     en Fedora registra el puerto en SELinux). Tu sesión abierta no se corta.
   - Aplica las reglas del homelab (`firewall.sh`) con el puerto y las tarjetas de aquí.
   - Copia **tus** reglas de UFW de la Pi (nginx, 8090, iperf…): cambia la IP de la Pi por la de
     esta máquina y `eth0`/`wlan0` por las tarjetas que elegiste, te las muestra y pregunta antes
     de agregarlas. No copia las del homelab ni las de SSH (ya están hechas para aquí).
     `--sin-firewall-pi` lo salta.
6. Te dice qué falta a mano: el DNS del módem si usabas AdGuard en la Pi, revisar Uptime Kuma.

## `./hl limpiar` — dejar la Pi como estaba

Córrelo **en la Pi**:

```bash
sudo ./scripts/limpiar-pi.sh --plan          # solo muestra qué quitaría
sudo ./scripts/limpiar-pi.sh                 # quita todo lo del homelab
```

Quita: contenedores, volúmenes e imágenes del homelab (Pi, PC, Immich, laboratorio; las
**fotos** de Immich no se borran, siguen en su carpeta del SSD),
servicios de systemd, nftables y el puente `br0`, reglas de UFW del homelab, SSH solo con
llave, NUT (regresa la configuración original), el agente de Wazuh y `wg0` si lo creó
`pi-vpn.sh`. Antes hace un último respaldo cifrado (`/root/homelab-respaldo-final`) y te pide
escribir `LIMPIAR`.

Opciones: `--borrar-datos` (también `.env`, `homelab.yml`, respaldos y Vaultwarden),
`--ssh` (quita también las reglas de SSH solo para el laboratorio; antes deja entrar desde la
LAN), `--quitar-docker` (desinstala Docker), `--sin-respaldo`, `--si` (sin preguntas).

**Nunca toca**: tu nginx (80/443 y 8090), el túnel `wg-hoshimy` ni tu `telegram-bot-api`.

Si AdGuard de la Pi era el DNS de la casa, cámbialo en el módem/Tenda **antes** de limpiar o
la casa se queda sin internet (el script te avisa).
