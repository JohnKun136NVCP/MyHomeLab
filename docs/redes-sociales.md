# Publicaciones programadas (Facebook, Instagram y canal de Telegram)

```
/programar 2026-11-20 18:00 fb,ig,tg ¡Nueva promo de temporada!
/programar mañana 09:30 todas Buenos días 🌞
/programar 25/12 08:00 tg Feliz Navidad
```
Para foto o video mándalo **con ese texto en el pie**. Cuando se publica (o la cancelas)
la foto/video se **borra de la Pi** para no llenar el disco; también se borran los archivos que
el servidor Bot API local guarda al recibirlos. Luego `/programadas`, `/cancelar ID`,
`/publicar_ya ID`. Al publicar te llega el resultado de cada red.

## Canal de Telegram
Agrega tu bot como **administrador** del canal y pon `social.telegram_channel: "@tu_canal"`.

## Facebook e Instagram (API oficial de Meta)
Requisitos: una **Página** de Facebook de la empresa y una cuenta de Instagram
**Business/Creator vinculada** a esa página.

1. https://developers.facebook.com → Crear app (tipo «Empresa»).
2. Agrega los productos *Facebook Login for Business* e *Instagram Graph API*.
3. En el **Explorador de la Graph API** pide un token de usuario con los permisos
   `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `business_management`,
   `instagram_basic`, `instagram_content_publish`.
4. Cámbialo por uno de larga duración y luego pide el **token de página**:
   `GET /me/accounts` → el `access_token` de tu página (este no caduca mientras la app
   y tu usuario sigan activos). Ponlo en `.env` como `META_PAGE_TOKEN`.
5. IDs para `homelab.yml`:
   - `facebook_page_id`: el `id` de tu página en `/me/accounts`.
   - `instagram_user_id`: `GET /{page-id}?fields=instagram_business_account`.

Mientras la app esté en modo desarrollo solo publica en páginas donde tú eres admin, que es
justo lo que necesitas; no hace falta pasar la revisión de Meta para uso propio.

### Notas
- Instagram exige una imagen en URL pública. El bot sube primero la foto a tu página como
  **no publicada** y usa la URL que Meta le asigna: no necesitas exponer la Raspberry.
- Instagram aquí acepta foto + texto (no texto solo ni video/reels).
- Facebook acepta texto, foto o video.
