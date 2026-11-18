# Fotos de la familia (Immich)

Immich es como Google Fotos, pero las fotos se quedan en la PC de la casa. Cada quien tiene
su cuenta, la app del celular sube las fotos en segundo plano y nadie ve las fotos de otro
si no se las comparte.

| Qué | Dónde |
|---|---|
| Web | `http://IP-de-la-PC:2283` (botón «📸 Fotos (Immich)» en Inicio del panel) |
| Fotos en disco | la carpeta que elegiste al instalar (por defecto `/srv/fotos`) |
| Base de datos | `hosts/server/immich/postgres` |
| Instalar / actualizar | `./scripts/instalar-immich.sh` / `./scripts/instalar-immich.sh --actualizar` |

## Instalarlo (en la PC)

```bash
./scripts/instalar-immich.sh
```

Te pregunta en qué carpeta guardar las fotos (pon una del disco grande), baja la versión
oficial de Immich, genera la contraseña de su base de datos, abre el puerto 2283 en el
firewall y agrega el botón al panel. Si la PC tiene gráfica Intel, el reconocimiento de
caras y objetos la usa.

`install-server.sh` y `migrar.sh` te lo ofrecen solos.

## Primera vez (el administrador)

1. Abre `http://IP-de-la-PC:2283` y crea **tu** cuenta: la primera cuenta es la de administrador.
2. **Administración → Usuarios → Crear usuario** para cada persona de la casa (correo puede
   ser inventado, p. ej. `mama@casa`; lo que importa es la contraseña).
3. **Administración → Ajustes → Copia de la base de datos**: déjala diaria (viene así).
   `respaldo-server.sh` mete la copia más reciente en el respaldo cifrado de la PC.
4. Opcional: en **Almacenamiento** activa la «plantilla de almacenamiento» para que las
   fotos queden por año/mes en el disco.

## En el celular (cada persona)

1. Instala **Immich** desde Play Store o App Store.
2. En «URL del servidor» escribe `http://IP-de-la-PC:2283` y entra con tu correo y contraseña.
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
./scripts/instalar-immich.sh --actualizar             # baja la versión nueva y reinicia
```

Para actualizar, lee antes las notas de la versión en GitHub (Immich avisa cuando un cambio
necesita pasos extra). El respaldo de la PC guarda la base de datos; las fotos en sí van
en tu disco: si quieres una segunda copia, sincronízalas a otro disco (por ejemplo, con
`rsync -a /srv/fotos/ /media/otro-disco/fotos/`).
