# VPN con WireGuard (VPS como centro)

## Si ya tienes un túnel Pi ↔ VPS (recomendado: reutilízalo)
No hace falta wg-easy. Con tu túnel (por ejemplo `wg-hoshimy`):
1. En el VPS: `sudo wg show` → la IP del VPS en el túnel (ej. `10.66.0.1`). Ponla en `.env`
   como `VPN_VPS_IP` y levanta solo las métricas:
   `docker compose -f hosts/vps/docker-compose.yml --env-file .env up -d`
2. En el UFW del VPS: `sudo ufw allow in on wg-hoshimy from IP_DE_LA_PI to any port 9100 proto tcp`
3. En `config/homelab.yml` de la Pi: `nodes: - {name: vps, target: 10.66.0.1:9100}` y, si
   quieres, un `ping_targets` al VPS. Reinicia: `docker compose ... up -d`.
4. Prometheus corre en Docker en la Pi y sale por el túnel del host sin configurar nada más.

El resto de esta guía es para crear una VPN nueva con wg-easy (web + QR para tu
teléfono y laptop): perfil `--profile wg-easy`.

## VPN nueva con wg-easy

Muchos internet de casa en México (Telmex, Izzi, Totalplay) están detrás de **CGNAT**: no
puedes abrir puertos hacia tu casa. Por eso la VPN vive en el **VPS** y todos se conectan
a él: la Pi, la PC, tu laptop y tu teléfono.

```
Laptop/teléfono ─┐
Raspberry Pi ────┼──► VPS 10.8.0.1 (wg-easy)
PC Linux ────────┘
```

## En el VPS
1. Abre el puerto **51820/udp** en el firewall del proveedor y en `ufw`:
   `sudo ufw allow 51820/udp`
2. En `.env` del VPS: `WG_HOST` (IP pública o dominio) y `WG_PASSWORD_HASH`
   (`docker run --rm ghcr.io/wg-easy/wg-easy:14 wgpw 'tu-clave'`, y duplica los `$`).
3. `docker compose -f hosts/vps/docker-compose.yml --env-file .env up -d`
4. La web de wg-easy escucha en el puerto 51821. **No la abras a internet**: entra con un
   túnel SSH (`ssh -L 51821:localhost:51821 usuario@vps` → http://localhost:51821) o,
   cuando ya estés en la VPN, por `http://10.8.0.1:51821` permitiendo solo wg0:
   `sudo ufw allow in on wg0 to any port 51821,9100 proto tcp`

## Clientes
- **Raspberry**: crea el cliente «raspberry» en wg-easy, descarga el `.conf` y
  `sudo ./scripts/pi-vpn.sh raspberry.conf` (queda siempre conectada, solo la red 10.8.0.0/24
  pasa por el túnel). Anota su IP (p. ej. 10.8.0.2).
- **Laptop / teléfono**: app WireGuard → importar el QR o el `.conf`.
- Desde fuera de casa entra a la web del homelab con `https://10.8.0.2` (la IP de la Pi en la VPN).

## Métricas del VPS
node-exporter del VPS solo escucha en `10.8.0.1:9100`; la Prometheus de la Pi lo lee por el
túnel (`infrastructure.nodes` en `homelab.yml`). Si el VPS cae, llega la alerta
`NodoCaido`; el reporte diario y `/vps` muestran CPU, RAM, disco y tráfico.

## Llegar a toda la red de casa (APs, cámaras) desde fuera
Por defecto solo llegas a la Pi. Para entrar a `192.168.0.0/24` a través de ella:
1. En la Pi: `sudo sysctl -w net.ipv4.ip_forward=1` (y en `/etc/sysctl.conf`), y
   `sudo iptables -t nat -A POSTROUTING -s 10.8.0.0/24 -o eth0 -j MASQUERADE` (br0 en modo puente).
2. En el VPS, en el peer de la Pi agrega `192.168.0.0/24` a sus AllowedIPs. wg-easy v14 no
   lo permite desde la web: edita `/etc/wireguard/wg0.conf` en el volumen o usa wg-easy v15,
   que sí tiene «Allowed IPs» por cliente.
3. En los clientes, `AllowedIPs = 10.8.0.0/24, 192.168.0.0/24`.
