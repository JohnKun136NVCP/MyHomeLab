# Fotos de la familia (Immich)

Immich es como Google Fotos, pero las fotos se quedan en casa. Cada quien tiene su cuenta,
la app del celular sube las fotos en segundo plano y nadie ve las fotos de otro si no se las
comparte. Puede vivir en la **Raspberry** (64 bits, 8 GB de RAM, fotos en el SSD) o en la **PC**,
y pasarse de una a otra cuando quieras.

| Qué | Dónde |
|---|---|
| Web | `http://IP:2283` (botón «📸 Fotos (Immich)» en Inicio del panel) |
| Fotos en disco | la carpeta que elegiste al instalar (por defecto `/srv/fotos`; en la Pi, ponla en el SSD) |
| Base de datos | `hosts/server/immich/postgres` (si el repo está en la microSD, junto a las fotos) |
| Estado | `./scripts/immich.sh --status` |
| Actualizar | `./scripts/immich.sh --update` |

## Instalarlo

```bash
./scripts/immich.sh
```

Funciona igual en la Pi y en la PC. Te pregunta en qué carpeta guardar las fotos, baja la versión
oficial de Immich, genera la contraseña de su base de datos, abre el puerto 2283 en el firewall
y agrega el botón al panel. En una PC con gráfica Intel el reconocimiento de caras la usa.

En la Pi todo funciona, incluido el reconocimiento de caras y la búsqueda inteligente, solo que
lento: la primera vez que procesa muchas fotos puede tardar horas (luego, solo lo nuevo). Las
fotos se suben y se ven al instante; lo lento es lo de «quién sale en la foto».

La copia diaria de la base que hace Immich entra en el respaldo cifrado del homelab
(`./hl backup`), estés en la Pi o en la PC. Las fotos, por su tamaño, no: ya están en el SSD.

## Cuando tengas la PC: dos caminos

### A. La forma intermedia (las fotos se quedan en la Pi)

La Pi sigue recibiendo fotos las 24 horas; la PC hace el trabajo pesado del reconocimiento.

```bash
# En la PC
./scripts/immich.sh --ml-only 192.168.1.69          # IP de la Pi
# En la Pi
./scripts/immich.sh --ml-remote http://IP-de-la-PC:3003
```

El segundo comando te dice el último paso, en la web de Immich: **Administración →
Configuración → Aprendizaje automático → URL**, pon primero la de la PC y después
`http://immich-machine-learning:3003` (la de la Pi). Si la PC está apagada, Immich usa la de la
Pi y nada se detiene. La PC instala la **misma versión** que la Pi; si actualizas Immich en la
Pi, vuelve a correr `--ml-only` en la PC. Para regresar todo a la Pi: `--ml-local`.

### B. Mudar Immich completo a la PC

```bash
# En la PC
./scripts/immich.sh --pull-from pi@192.168.1.69         # o pi@IP:2222 si tu SSH usa otro puerto
```

Qué hace:

1. La Pi pausa Immich (nadie sube mientras se copia) y exporta su base.
2. La PC instala **la misma versión** que tiene la Pi, con la misma contraseña de base.
3. Copia fotos y videos con `rsync`: si se corta, vuelve a correrlo y sigue donde se quedó.
4. Restaura la base con el procedimiento oficial de Immich y lo levanta.
5. Cambia el botón «Fotos» del panel a la PC y apaga Immich en la Pi.

Si algo falla a medio camino, Immich vuelve a funcionar en la Pi como estaba. Cuentas, álbumes,
caras y fotos quedan iguales. En cada celular solo cambias la dirección del servidor (Cerrar
sesión → `http://IP-de-la-PC:2283` → entrar); lo que ya se subió no se sube otra vez.

Las fotos siguen en el SSD de la Pi hasta que tú las borres: revisa primero que en la PC esté
todo. `./hl migrate` hace esta mudanza sola si la Pi tiene Immich, y `install-server.sh` te
ofrece los dos caminos.

Para copiar fotos que son de root, la Pi da un permiso temporal a `rsync` en
`/etc/sudoers.d/90-homelab-immich-mudanza`; se quita al terminar (y `./hl clean` lo quita si
se quedó).

## Primera vez (el administrador)

1. Abre `http://IP:2283` y crea **tu** cuenta: la primera cuenta es la de administrador.
2. **Administración → Usuarios → Crear usuario** para cada persona de la casa (correo puede
   ser inventado, p. ej. `mama@casa`; lo que importa es la contraseña).
3. **Administración → Ajustes → Copia de la base de datos**: déjala diaria (viene así).
   `backup-server.sh` mete la copia más reciente en el respaldo cifrado de la PC.
4. Opcional: en **Almacenamiento** activa la «plantilla de almacenamiento» para que las
   fotos queden por año/mes en el disco.

## En el celular (cada persona)

1. Instala **Immich** desde Play Store o App Store.
2. En «URL del servidor» escribe `http://IP:2283` (la de la Pi o la PC, donde viva Immich) y entra con tu correo y contraseña.
3. Toca la nube ☁ arriba a la derecha → elige los álbumes (Cámara, WhatsApp…) → **Activar respaldo**.
4. Activa **Respaldo en segundo plano** y, en Android, quítale a Immich la «optimización de batería»
   para que el sistema no lo detenga.
5. Si no quieres gastar datos: ajustes del respaldo → «solo con Wi-Fi».

Las fotos ya subidas puedes borrarlas del celular para liberar espacio: en la app,
**Liberar espacio** borra del teléfono solo lo que ya está en la PC.

## Fuera de casa

La app sube cuando vuelves a la Wi-Fi de la casa. Si quieres que suba desde fuera, conecta
el celular a la VPN del homelab (Web → **VPN**, código QR) y pon en la app la misma URL.
No abras el puerto 2283 en el módem.

## Compartir

- **Álbum compartido**: crea un álbum → «Compartir» → elige a la persona.
- **Compartir con la pareja** («Partner sharing», en tu perfil): la otra persona ve todas tus fotos.
- **Enlace**: sirve para mandar un álbum a alguien sin cuenta; ponle fecha de caducidad.

## Si algo falla

```bash
cd hosts/server/immich && docker compose ps          # ¿todos «running»?
docker compose logs --tail 50 immich-server
./scripts/immich.sh --update             # baja la versión nueva y reinicia
```

Para actualizar, lee antes las notas de la versión en GitHub (Immich avisa cuando un cambio
necesita pasos extra). El respaldo de la PC guarda la base de datos; las fotos en sí van
en tu disco: si quieres una segunda copia, sincronízalas a otro disco (por ejemplo, con
`rsync -a /srv/fotos/ /media/otro-disco/fotos/`).
