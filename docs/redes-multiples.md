# Varias redes: una interfaz por red, o una sola Wi-Fi (roaming)

**Recomendado:** una interfaz por red, sin roaming. Ejemplo: `eth0` (cable) al módem y
`wlan0` al Wi-Fi del Tenda del lab; ambas redes se escanean en cada ciclo. Es lo que trae
`config/homelab.example.yml`. Nunca pongas dos interfaces en la MISMA red (dos rutas por
defecto, ARP flux y conexiones que se cortan): la del lab lleva `ipv4.never-default yes`.

El roaming de abajo es para cuando solo tienes una Wi-Fi para varias redes.

## Roaming con una sola Wi-Fi

Mi casa tiene dos segmentos:

| Red | SSID | Segmento | Uso |
|---|---|---|---|
| casa | Wi-Fi del módem | 192.168.1.0/24 | red principal y de la familia |
| laboratorio | Wi-Fi del Tenda 1 | 192.168.100.0/24 | laboratorio de redes (Tenda en modo router) |
| oficinas | Wi-Fi del Tenda 2 | 192.168.2.0/24 | oficinas y cuartos (Tenda en modo router) |

El escaneo ARP solo ve la red donde está conectada la Pi, y el NAT del Tenda no deja ver
su red desde la del módem. Con una sola tarjeta Wi-Fi, la Pi **vive en «casa»** y cada
`hop_every` segundos (30 min por defecto) **brinca al SSID del laboratorio**, lo escanea,
hace ping a su infraestructura y regresa. Fuera de casa está unos 30–60 s.

## Configurar
1. Guarda una vez el perfil de cada Wi-Fi en NetworkManager de la Pi:
   ```bash
   sudo nmcli dev wifi connect "INFINITUM_XXXX" password "clave-del-modem"
   sudo nmcli dev wifi connect "Tenda_LAB" password "clave-del-tenda"
   sudo nmcli dev wifi connect "INFINITUM_XXXX"          # regresa a casa
   # Que la Pi siempre prefiera casa al arrancar:
   sudo nmcli con modify "INFINITUM_XXXX" connection.autoconnect-priority 10
   sudo nmcli con modify "Tenda_LAB" connection.autoconnect-priority 0
   ```
   Si el nombre del perfil no es igual al SSID, ponlo en `connection:` de esa red.
2. En `config/homelab.yml`, la sección `network.networks` con cada SSID y su `cidr`, y
   `roaming.enabled: true` (ver el ejemplo).
3. El Tenda va en `access_points` con `network: laboratorio`, su IP del lab (`ip`) y la IP que
   le dio el módem (`wan_ip`). Desde casa se le hace ping por `wan_ip`; en el mapa aparece
   como rama propia con sus dispositivos.

## Qué pasa mientras la Pi está en el laboratorio
- La web, Grafana y el bot siguen funcionando, pero **desde la red de casa no se alcanzan**
  esos 30–60 s (la Pi cambió de IP). El laboratorio también tiene internet, así que el bot
  sigue conectado a Telegram (las conexiones se cortan un instante y se reintentan).
- Los dispositivos de casa **no** se marcan como desconectados: cada escaneo solo actualiza
  la red que está viendo.
- Si no logra entrar al laboratorio te llega un aviso y vuelve a casa. Si no logra regresar a
  casa, es un aviso crítico (y NetworkManager reconecta solo por la prioridad del perfil).

## Ajustes útiles
- `notify_new_devices: false` en el laboratorio: ahí conectas y desconectas equipos seguido.
- `hop_every: 600` si quieres ver el lab más seguido (más cortes breves en casa).
- Si un día te desocupas el adaptador USB Wi-Fi: pon `interface: wlan1` en la red del
  laboratorio y `roaming.enabled: false` → ambas redes se escanean siempre. (Pide la
  regla que aísla las dos redes en la Pi; Docker activa el reenvío de paquetes.)

## Limitaciones
- Entre visitas no sabes qué pasa en el laboratorio (hasta 30 min de retraso).
- Por Wi-Fi no hay modo puente: sin tráfico por dispositivo ni bloqueos reales.

## Mapa de Wi-Fi (Red → Wi-Fi)

La Pi usa su Wi-Fi (aunque esté conectada por cable) para ver tus redes y las de los vecinos:
por cada SSID, sus puntos de acceso (BSSID y fabricante), banda, canal, señal y seguridad;
cuántas redes hay en cada canal y sugerencias («usa 1, 6 u 11 en 2.4 GHz», «ese canal lo
comparten 3 vecinos fuertes»). Si no dices cuáles son las tuyas, las adivina por los aparatos a
los que está conectada la Pi; para fijarlas:

```yaml
wifi:
  ssids: ["Casa", "Casa-Arriba"]
```

El router no le dice a la Pi a qué red se conecta cada equipo, así que al final de la página
eliges una vez a qué SSID va cada uno y queda en su tarjeta.

## Varios SSID con una sola Wi-Fi (roaming mixto)

Lo normal en la Pi: **casa por cable** (`eth0`) y **la Wi-Fi** (`wlan0`) para las demás. Si
hay más de un SSID que ver por la Wi-Fi (laboratorio, oficinas…), ponlos todos con
`interface: wlan0`: la Pi se queda en el primero, cada `roaming.hop_every` segundos visita los
otros (escanea y regresa) y casa por cable se sigue escaneando siempre.

```bash
./hl roam                     # qué redes tiene, si la Wi-Fi está guardada y si la ve
sudo ./hl roam add "OFICINAS" # guarda la contraseña SIN conectarse
./hl roam now                 # que dé la vuelta ya
```

Si dos SSID son del **mismo router y la misma red** (mismas IP 192.168.X.*, por ejemplo la
de 2.4 GHz y la de 5 GHz), no hace falta brincar: la Pi ya ve a todos sus equipos. Solo hace
falta para SSID con **otra red** (otro router, o una red de invitados aislada).

Lo más cómodo, si no quieres que la Pi se desconecte unos segundos: un adaptador Wi-Fi USB
(`wlan1`) fijo en la otra red, con `interface: wlan1` en esa red. Así no brinca nada.

## Tu caso: casa, laboratorio y oficinas

```bash
sudo ./hl roam fix                                   # pone el SSID real del laboratorio (al que está la Wi-Fi)
./hl roam network oficinas 192.168.2.0/24 "SSID-de-oficinas"
sudo ./hl roam add "SSID-de-oficinas"                # su contraseña, sin desconectarse
./hl restart netwatch && ./hl roam now
```

Agrega también `192.168.2.0/24` a `firewall.lan_networks` (y corre `sudo ./hl firewall`) para
que las oficinas puedan usar el DNS de la Pi y ver la página de estado.

## ¿Se conectó otro router? (Red → Wi-Fi → «Routers en tus redes»)

Cada 30 min la Pi pregunta en cada red **quién reparte IPs (DHCP)** y anota qué red ofrece
(**CIDR**), su puerta de enlace y su DNS. Si aparece uno que no es tu módem ni tus Tenda, te
llega **🛰 Otro router repartiendo IPs en «casa»: 192.168.0.1** con su red (p. ej.
`192.168.0.0/24`). Es lo que pasa cuando alguien conecta un router por su puerto LAN: los
equipos que le hagan caso se quedan sin tu internet. «Es mío» lo deja de marcar.

En el aire, cada 10 min se anotan los BSSID que se ven:
- **Red nueva con señal fuerte (≥ 60 %)** → casi seguro está dentro de la casa (📡). Si su MAC
  se parece a la de un equipo conectado, te dice su IP.
- **Tu SSID desde un aparato de otro fabricante** → posible **gemelo malvado** (🚨), el truco
  para robar contraseñas de Wi-Fi.

## Optimizar la Wi-Fi (Red → Wi-Fi → «Optimizar»)

Con lo que la Pi oye en el aire calcula el **canal más libre** para cada AP tuyo (1/6/11 en
2.4 GHz; 36-48 o 149-161 en 5 GHz, sin DFS), y revisa velocidad de enlace, señal débil,
seguridad vieja (WPA/TKIP limita a 54 Mbit/s), redes solo en 2.4 GHz y AP tuyos en el mismo
canal. El canal se cambia en la página de cada Tenda. Además:

- **⚡ Acelerar DNS** (AdGuard): consultas en paralelo, caché de 8 MB y caché optimista. Cada
  página abre muchos nombres: se nota.
- Lista para dejarla rápida: 20 MHz en 2.4 GHz, 80 MHz en 5 GHz, WPA2-AES, WMM, AP por cable
  en vez de repetidor, firmware al día, router alto y al centro.
