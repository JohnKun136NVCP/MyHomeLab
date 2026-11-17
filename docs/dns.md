# AdGuard Home: DNS de la casa

Bloquea publicidad, rastreadores y **dominios de malware/phishing** para todos los equipos,
y le dice a netwatch qué equipo pregunta qué. Si alguien intenta abrir un dominio peligroso,
te llega aviso al bot.

## Levantarlo
```bash
sudo docker compose -f hosts/pi/docker-compose.yml --env-file .env --profile dns up -d
```
1. Abre `http://IP-de-la-Pi:3002` y sigue el asistente. En «Interfaz web de administración»
   deja **todas las interfaces, puerto 3000** (por dentro; afuera es 3002). En «Servidor DNS»,
   puerto 53.
2. Crea usuario y contraseña y ponlos en web → ⚙ Ajustes → AdGuard (o en `.env`).
   netwatch encuentra AdGuard solo en `http://127.0.0.1:3002`; si lo cambiaste de puerto,
   pon la URL en Ajustes → Red.
3. En AdGuard → Configuración general activa **«Usar el servicio de navegación segura»**
   (eso detecta malware/phishing). Filtros → Listas de bloqueo: con la de AdGuard basta.
4. «Guardar y aplicar» en Ajustes (o `... up -d --force-recreate netwatch`).

## Que los equipos lo usen
- **Red de casa:** en el módem (`http://192.168.1.254`) busca DHCP → DNS primario = IP de la
  Pi. Si tu módem no deja cambiarlo (algunos de Telmex), ponlo en cada equipo a mano.
- **Laboratorio y oficinas:** en cada Tenda, DHCP → DNS = IP **WAN** de la Pi (`192.168.1.5`).
  Desde esas redes AdGuard verá como cliente al Tenda (NAT), no a cada equipo.
- Deja un DNS **secundario** externo solo si aceptas que a veces se salten el filtro; si la
  Pi se apaga sin secundario, la casa se queda sin DNS.

Bot: `/dns`. Web: tarjeta «DNS» en el Resumen.

## Control D como DNS de salida (filtro familiar)
AdGuard le pregunta a internet a través del servidor que elijas en **⚙ Ajustes → DNS de
salida**; se aplica al guardar, sin reiniciar nada:

| Opción | Qué bloquea |
|---|---|
| Control D: malware | sitios maliciosos |
| Control D: malware + anuncios | lo anterior y anuncios/rastreadores |
| **Control D familiar** | malware, anuncios y **contenido para adultos** (y pone YouTube en modo restringido) |
| Control D con tu perfil | lo que configures en controld.com (pon el ID de tu resolver) |

Así conservas lo mejor de los dos: AdGuard sigue registrando **qué equipo** pidió qué y
avisando al bot, y Control D aplica sus categorías. netwatch revisa cada 30 min que siga
puesto (por si reinstalas AdGuard).

**Solo para algunos equipos** (por ejemplo los de los niños): en AdGuard → Clientes →
agrega el equipo y en «Servidores DNS de subida» pon `https://freedns.controld.com/family`;
el resto de la casa sigue con el DNS general.

Ninguna de estas opciones quita los anuncios de YouTube (ver arriba).
