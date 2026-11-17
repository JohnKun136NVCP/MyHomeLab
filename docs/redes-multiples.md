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
