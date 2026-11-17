# Uptime Kuma y Vaultwarden (perfil «servicios»)

```bash
sudo docker compose -f hosts/pi/docker-compose.yml --env-file .env --profile servicios up -d
```

## Uptime Kuma — `http://IP-de-la-Pi:3001`
Monitorea páginas y servicios con historial: tu página de empresa, el VPS, el nginx del 8090,
la Bot API, etc. En «Configuración → Notificaciones» elige **Telegram** con el token de tu bot
y tu chat ID para recibir las caídas.

## Vaultwarden (gestor de contraseñas) — `https://IP-de-la-Pi:8443/vault/`
Compatible con las apps y extensiones de **Bitwarden** (en la app: «Autohospedado» y esa URL).
1. Entra y **crea tu cuenta**.
2. En `.env` pon `VW_SIGNUPS_ALLOWED=false` y recrea: `... up -d --force-recreate vaultwarden`.
3. Panel de administración: `/vault/admin` con `VW_ADMIN_TOKEN` de `.env`.
4. Desde fuera de casa: solo por la VPN (no abras puertos del módem).

**Respaldo (importante):** tus contraseñas viven en `VW_DATA` (por defecto
`/mnt/respaldos/vaultwarden`, en el disco USB). Agrégalo a lo que respaldas y guarda aparte
el «Exportar bóveda» cifrado de la app de vez en cuando. Si pierdes eso, pierdes las contraseñas.
Las apps de Bitwarden guardan una copia local, así que si la Pi se apaga sigues viéndolas.
