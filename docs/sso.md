# Inicio de sesión único (Authelia)

Entras una vez en `https://auth.casa.lan:8443` y ya quedas dentro del panel, Grafana,
Uptime Kuma y AdGuard. Los administradores además confirman con un código de 6 dígitos (2FA).

```bash
./hl sso                     # pregunta el dominio (casa.lan)
./hl sso --no-2fa           # sin código para administradores (no recomendado)
./hl sso --remove            # regresa a como estaba
```

| Dirección | Qué es | Quién entra |
|---|---|---|
| `https://panel.casa.lan:8443` | el panel de siempre | todas las cuentas (cada una ve lo de su rol) |
| `https://auth.casa.lan:8443` | portal de Authelia (entrar/salir, registrar el 2FA) | todos |
| `https://grafana.casa.lan:8443` | Grafana, ya con tu sesión | administradores |
| `https://kuma.casa.lan:8443` | Uptime Kuma | administradores |
| `https://adguard.casa.lan:8443` | AdGuard | administradores |

(Si Caddy usa el 443 de la Pi, las direcciones van sin `:8443`.)

**Por IP todo sigue igual**: `https://192.168.1.5:8443` usa el inicio de sesión propio del
panel. Si algo sale mal con Authelia, siempre puedes entrar por ahí.

## Cuentas

No hay cuentas nuevas que administrar: las de **Ajustes → Cuentas** se copian solas a
`config/authelia/users.yml` (mismo hash bcrypt) en menos de un minuto, y Authelia las relee.
Cambiar la contraseña en «Mi cuenta» la cambia en los dos lados. Los roles pasan como grupos:
`admin`, `registros`, `familia`.

## El código de 2FA

La primera vez que un administrador entra, Authelia pide registrar un dispositivo:

1. Toca «Registrar dispositivo» → «Contraseña de un solo uso».
2. Authelia «manda un correo» con un código de verificación. Como el homelab no tiene correo,
   se queda en `config/authelia/notification.txt` y **el panel te lo reenvía por Telegram,
   solo a ti** (nunca a cuentas «ve registros»).
3. Escanea el QR con Aegis, Google Authenticator o el TOTP de Bitwarden/Vaultwarden.

## Cómo está armado

- `sso.sh` genera `config/authelia/configuration.yml` y `config/caddy/sso/sso.caddy`
  (los dos fuera de git), los secretos en `.env` y el perfil `sso`.
- Caddy pregunta a Authelia (`forward_auth`) antes de cada página. Si Authelia aprueba, Caddy
  le pasa al panel `Remote-User` y una cabecera secreta (`SSO_SECRET`); sin esa cabecera el
  panel no le cree a `Remote-User`. Caddy borra ambas cabeceras si vienen del navegador.
- El panel sigue decidiendo qué ve cada rol, igual que con su propio login.
- Respaldos (`/api/backup`), instaladores (`/setup`), el certificado y Vaultwarden no pasan por
  Authelia: tienen su propio token o inicio de sesión. `/vault/admin` sí (solo administradores).
- DNS: si usas AdGuard (perfil `dns`), el script agrega `*.casa.lan → IP de la Pi`. Si no,
  agrégalo tú en AdGuard o en el archivo `hosts` de cada equipo.
- El certificado es la misma CA de Caddy (`./hl ca`): ya instalada, sirve para los subdominios.

## ¿No entra con casa.lan? Alternativas

`casa.lan` solo funciona en equipos que usan la Pi (AdGuard) como DNS. Si no te deja entrar:

| Opción | Cómo | Ventaja / límite |
|---|---|---|
| **sslip.io** (la más fácil) | `./hl sso --sslip` → `https://panel.192-168-1-69.sslip.io:8443` | Funciona en todos los equipos sin configurar DNS. Necesita internet para resolver el nombre; algunos módems bloquean respuestas con IPs de la casa («DNS rebinding») |
| **Tu propio dominio** | En el DNS de tu dominio crea `*.casa.tudominio.com` → `192.168.1.69` y corre `./hl sso --domain casa.tudominio.com` | Lo más profesional y estable; igual el aviso de rebinding en algunos módems |
| **Archivo hosts** | En cada computadora agrega `192.168.1.69 auth.casa.lan panel.casa.lan grafana.casa.lan kuma.casa.lan adguard.casa.lan` | No necesita internet; no sirve en celulares |
| **Por IP** | `https://192.168.1.69:8443` | Siempre funciona, con el inicio de sesión propio del panel (sin Authelia) |

Al cambiar de dominio vuelve a entrar una vez a `https://auth.NUEVO` y registra otra vez el 2FA.

## Si algo falla

`./hl sso --check` revisa el DNS de `auth.` y `panel.`, que Authelia y Caddy estén bien y que el
panel mande al portal. Si el navegador muestra **about:blank#blocked**: escribe tú la dirección
`https://panel.casa.lan:8443` en una pestaña nueva (no desde un enlace), apaga el «DNS seguro»
del navegador en ese equipo para que use la Pi como DNS, instala el certificado de la casa y
prueba en una ventana de incógnito (si ahí funciona, es una extensión).


```bash
./hl logs authelia
cat config/authelia/users.yml          # ¿están tus cuentas?
nslookup panel.casa.lan                 # ¿resuelve a la Pi?
```

Bloqueos por intentos fallidos: 5 en 10 min bloquean 15 min a ese usuario.
