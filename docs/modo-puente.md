# Modo puente: ver todo el tráfico y bloquear de verdad

Por defecto la Pi está conectada como un equipo más (**modo pasivo**). Así ya funciona la
lista de dispositivos (ARP/nmap), las latencias, la detección de ARP spoofing y los avisos
de dispositivos nuevos. Pero el tráfico de tu teléfono va del AP al módem sin pasar por la
Pi, así que no puede medirlo ni cortarlo.

En **modo puente** la Pi queda físicamente entre el módem y tus AP:

```
Módem ──cable── eth0 [ Raspberry Pi: br0 ] eth1 (USB 3.0 Gigabit) ──cable── switch ── AP Tenda 1
                                                                            └────── AP Tenda 2
```

Con eso obtienes:
- tráfico de subida/bajada **por dispositivo** (contadores nftables por MAC → Grafana);
- Suricata viendo **todo** lo que entra y sale de la casa;
- **ACL reales**: bloquear una MAC (siempre o en horario) corta su internet al instante;
- cuarentena automática de dispositivos desconocidos (`anomalies.quarantine_unknown`).

## Qué necesitas
- Un adaptador **USB 3.0 → Gigabit Ethernet** (en el puerto azul de la Pi 4).
- Un switch si tus 2 AP van cableados al módem (los dos cuelgan del switch).
- La Pi 4 aguanta cerca de 900 Mbps en puente; con Suricata activo calcula 300–500 Mbps.
  Si tu plan es más rápido, Suricata se vuelve el cuello de botella: déjalo apagado o
  muévete a un mini PC con 2 puertos.

## Pasos
1. Conecta: módem → `eth0` de la Pi, adaptador USB (`eth1`) → switch/AP.
2. `sudo ./scripts/pi-bridge.sh activar eth0 eth1`
3. En `config/homelab.yml`: `bridge_mode: true` y `interface: br0`.
4. En `.env`: `CAPTURE_INTERFACE=br0` y revisa `HOMELAB_HOST` (`ip -4 addr show br0`).
5. `docker compose -f hosts/pi/docker-compose.yml --env-file .env --profile ids up -d`

El script también crea `homelab-bridge-fw.service`: Docker pone la cadena FORWARD de
iptables en DROP y, sin esa regla, el tráfico del puente se bloquearía.

## Si algo sale mal
- **Plan B inmediato:** conecta el cable del módem directo al switch/AP. La casa vuelve a
  tener internet sin la Pi.
- `sudo ./scripts/pi-bridge.sh desactivar` deja la Pi como antes.
- `sudo ./scripts/pi-bridge.sh estado` muestra el puente y la tabla `bridge homelab`.

## Cómo funciona por dentro
netwatch genera una tabla `bridge homelab` de nftables con un contador `up_<mac>` y
`down_<mac>` por dispositivo y un conjunto `blocked` con las MAC bloqueadas en ese momento.
Solo la vuelve a cargar cuando cambia la lista (para no reiniciar los contadores).
Prometheus lee los contadores y las reglas `TraficoAnomaloDispositivo` comparan los
últimos 10 minutos contra el promedio de 7 días.
