# Suricata (IDS) en la red de casa y el laboratorio

Vigila todo lo que pasa por las interfaces de la Pi y avisa al bot de alertas graves
(malware, escaneos, exploits). Las alertas aparecen en web → Eventos y en Grafana.

## Activar
1. **⚙ Ajustes → Servicios**: marca `ids` e «Interfaces de Suricata» = `eth0 wlan0`
   (casa por cable y laboratorio por Wi-Fi). En modo puente: `br0`.
2. «Guardar y aplicar». Revisa que arrancó en las dos:
   ```bash
   sudo docker compose -f hosts/pi/docker-compose.yml --env-file .env logs --tail 20 suricata
   # «Suricata escuchando en: -i eth0 -i wlan0»
   ```
3. Descarga las reglas (la primera vez y luego cada semana):
   ```bash
   sudo docker compose -f hosts/pi/docker-compose.yml --env-file .env exec suricata suricata-update
   sudo docker compose -f hosts/pi/docker-compose.yml --env-file .env restart suricata
   ```

## Qué ve y qué no
- Sin modo puente ve el tráfico **de la Pi** y el broadcast/multicast de cada red (ARP, mDNS,
  DHCP, escaneos que lleguen a la Pi). No ve lo que tu teléfono habla con internet.
- En modo puente (`br0`) ve **todo** lo que entra y sale de la casa.
- En la Pi 4 consume CPU: si notas lentitud, deja solo `wlan0` (el laboratorio).
