# No-break (UPS) con NUT

La Pi lee el no-break por su cable USB. Cuando se va la luz te avisa por Telegram, y si la
batería se acaba apaga todo **en orden** (primero la PC, luego la Pi) para que el SSD y las
bases de datos no se dañen.

```bash
sudo ./hl ups                       # detecta el no-break y lo configura
sudo ./hl ups --shutdown-after 10        # además: apágate si en 10 min no regresa la luz
sudo ./hl ups --test              # manda un aviso de prueba al bot
```

| Qué pasa | Aviso |
|---|---|
| Se va la luz | ⚡ «Se fue la luz en casa…» (con sonido) |
| Regresa | 🔌 «Regresó la luz» |
| Batería baja | 🪫 y la Pi se apaga sola |
| Se desconecta el USB | ⚠ «perdió contacto con el no-break» |
| El no-break pide batería nueva | 🔋 |

En el panel, **Inicio** muestra la tarjeta «No-break» (carga, estado y minutos de batería) y
Prometheus guarda `homelab_ups_*` para graficarlo en Grafana.

## Cómo funciona

- `nut-scanner` busca el no-break por USB y escribe `/etc/nut/ups.conf` (casi todos los de
  casa funcionan con `usbhid-ups`; si el tuyo no, prueba `driver = nutdrv_qx`).
- `upsd` escucha solo en la propia Pi (`127.0.0.1:3493`), salvo que permitas una PC.
- `upsmon` llama a `/etc/nut/homelab-notify.sh` en cada cambio; ese script le avisa al bot.
- Los archivos originales de `/etc/nut` quedan como `*.homelab-orig`; `./hl clean` los regresa.

## La PC en el mismo no-break

```bash
# En la Pi: deja que la PC lea el no-break (abre 3493 solo para ella)
sudo ./hl ups --allow 192.168.1.50
sudo cat /etc/nut/.homelab-pass           # contraseña que pide la PC
# En la PC:
sudo ./scripts/ups.sh --client 192.168.1.5
```

Cuando la batería llega a «baja», la Pi le ordena a la PC apagarse, espera y luego se apaga
ella. Para que todo vuelva a prender solo al regresar la luz:

- **PC**: en el BIOS, «Restore on AC power loss» → *Power On*.
- **Pi**: prende sola al recibir corriente.
- Revisa que el no-break esté en modo «encender al volver la corriente» (casi todos lo hacen).

## Si algo falla

```bash
upsc ups@localhost                      # ¿lee el no-break?
sudo journalctl -u 'nut-driver*' -u nut-server -u nut-monitor -n 50
lsusb                                   # ¿aparece el no-break?
```
