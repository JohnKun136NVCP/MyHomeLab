#!/usr/bin/env bash
# ======================================================================
#  Linux Guardian — restore.sh  ("máquina del tiempo")
#  Reinstala apps, dependencias y configuraciones desde un respaldo.
#
#  Uso:   ./restore.sh            pregunta sección por sección
#         ./restore.sh --yes      instala todo sin preguntar
#         ./restore.sh --dry-run  solo muestra lo que haría
#  Pensado para Debian / Ubuntu / Mint / Pop!_OS (apt + dpkg).
# ======================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PK="$HERE/packages"
YES=0; DRY=0
for a in "$@"; do
  case "$a" in
    -y|--yes) YES=1 ;;
    -n|--dry-run) DRY=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
  esac
done

c_ok=$'\e[32m'; c_warn=$'\e[33m'; c_err=$'\e[31m'; c_b=$'\e[1;36m'; c_0=$'\e[0m'
say()  { printf '%s\n' "${c_b}==>${c_0} $*"; }
ok()   { printf '%s\n' "  ${c_ok}✔${c_0} $*"; }
warn() { printf '%s\n' "  ${c_warn}⚠${c_0} $*"; }
STEP=0
step() { STEP=$((STEP+1)); say "[$STEP] $*"; }
err()  { printf '%s\n' "  ${c_err}✘${c_0} $*"; }
ask()  {  # ask "pregunta"  -> 0 si sí
  [[ $YES -eq 1 ]] && return 0
  local r; read -r -p "  ¿$1? [S/n] " r </dev/tty
  [[ -z "$r" || "$r" =~ ^[sSyY] ]]
}
doit() {  # ejecuta (o solo muestra en --dry-run)
  if [[ $DRY -eq 1 ]]; then echo "    [dry-run] $*"; return 0; fi
  "$@"
}

if [[ $EUID -eq 0 ]]; then
  err "No lo corras como root. Córrelo como tu usuario; pedirá sudo cuando haga falta."
  exit 1
fi
if ! command -v apt-get >/dev/null; then
  err "Este script usa apt (Debian/Ubuntu/Mint). En otra distro instala a mano con packages/*.txt"
  exit 1
fi

say "Respaldo de: $(grep -o '"host": *"[^"]*"' "$HERE/manifest.json" 2>/dev/null | cut -d'"' -f4) \
($(grep -o '"created": *"[^"]*"' "$HERE/manifest.json" 2>/dev/null | cut -d'"' -f4))"
[[ $DRY -eq 0 ]] && { sudo -v || exit 1; }
# Mantiene vivo sudo mientras corre el script
[[ $DRY -eq 0 ]] && { while true; do sudo -n true; sleep 50; kill -0 "$$" || exit; done 2>/dev/null & }

is_installed() { dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "install ok installed"; }
in_list()      { grep -qx "$1" "$PK/apt-manual.txt" 2>/dev/null; }
app_source()   { awk -F'\t' -v n="$1" '$1==n{print $2}' "$PK/apps.txt" 2>/dev/null; }

APT_UPDATED=0
apt_update() { [[ $APT_UPDATED -eq 1 ]] || { doit sudo apt-get update -qq; APT_UPDATED=1; }; }

# ----------------------------------------------------------------------
# Instala un .deb revisando primero sus dependencias
# ----------------------------------------------------------------------
install_deb_with_deps() {
  local deb="$1" missing=() group alt satisfied
  local name; name="$(dpkg-deb -f "$deb" Package 2>/dev/null)"
  say "Instalando $name desde $(basename "$deb")"
  local depends; depends="$(dpkg-deb -f "$deb" Depends 2>/dev/null)"
  IFS=',' read -ra groups <<< "$depends"
  for group in "${groups[@]}"; do
    satisfied=0; first=""
    IFS='|' read -ra alts <<< "$group"
    for alt in "${alts[@]}"; do
      alt="$(sed -E 's/\(.*\)//; s/:.*//; s/[[:space:]]//g' <<< "$alt")"
      [[ -z "$alt" ]] && continue
      [[ -z "$first" ]] && first="$alt"
      if is_installed "$alt"; then satisfied=1; break; fi
    done
    if [[ $satisfied -eq 1 ]]; then ok "dependencia presente: ${group// /}"
    elif [[ -n "$first" ]]; then warn "falta: ${group// /}"; missing+=("$first"); fi
  done
  apt_update
  if [[ ${#missing[@]} -gt 0 ]]; then
    say "Instalando ${#missing[@]} dependencias faltantes: ${missing[*]}"
    # Una por una para que un paquete virtual no tumbe a las demás
    for m in "${missing[@]}"; do doit sudo apt-get install -y "$m" >/dev/null 2>&1 || warn "apt no resolvió '$m' por nombre; lo intentará con el .deb"; done
  fi
  # apt instala el .deb y resuelve cualquier dependencia restante
  if doit sudo apt-get install -y "$deb"; then ok "$name instalado"
  else
    doit sudo dpkg -i "$deb"; doit sudo apt-get -f install -y && ok "$name instalado (con apt -f)"
  fi
}

download_deb() {  # download_deb URL archivo
  local url="$1" out="$2"
  doit curl -fL --retry 3 -o "$out" "$url" || doit wget -O "$out" "$url"
}

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# ----------------------------------------------------------------------
# 1. Herramientas base
# ----------------------------------------------------------------------
step "Herramientas base (curl, git, 7zip, gpg)"
if ask "Instalar herramientas base"; then
  apt_update
  doit sudo apt-get install -y curl wget git gpg ca-certificates apt-transport-https \
       software-properties-common >/dev/null && ok "listo"
  doit sudo apt-get install -y 7zip >/dev/null 2>&1 || doit sudo apt-get install -y p7zip-full >/dev/null
fi

# ----------------------------------------------------------------------
# 2. Repositorios de terceros (Brave, VS Code)
# ----------------------------------------------------------------------
step "Repositorios de terceros"
if in_list brave-browser || [[ "$(app_source Brave)" == "repo" ]]; then
  if is_installed brave-browser; then ok "Brave ya está instalado"
  elif ask "Agregar repositorio oficial de Brave e instalarlo"; then
    doit sudo curl -fsSLo /usr/share/keyrings/brave-browser-archive-keyring.gpg \
      https://brave-browser-apt-release.s3.brave.com/brave-browser-archive-keyring.gpg
    [[ $DRY -eq 0 ]] && echo "deb [signed-by=/usr/share/keyrings/brave-browser-archive-keyring.gpg] https://brave-browser-apt-release.s3.brave.com/ stable main" \
      | sudo tee /etc/apt/sources.list.d/brave-browser-release.list >/dev/null
    APT_UPDATED=0; apt_update
    doit sudo apt-get install -y brave-browser && ok "Brave instalado"
  fi
fi
if in_list code || [[ "$(app_source "VS Code")" == "repo" ]]; then
  if is_installed code; then ok "VS Code ya está instalado"
  elif ask "Agregar repositorio oficial de Microsoft e instalar VS Code"; then
    if [[ $DRY -eq 0 ]]; then
      curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > "$TMP/ms.gpg"
      sudo install -D -o root -g root -m 644 "$TMP/ms.gpg" /usr/share/keyrings/microsoft.gpg
      echo "deb [arch=amd64,arm64,armhf signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/repos/code stable main" \
        | sudo tee /etc/apt/sources.list.d/vscode.list >/dev/null
    fi
    APT_UPDATED=0; apt_update
    doit sudo apt-get install -y code && ok "VS Code instalado"
  fi
fi

# ----------------------------------------------------------------------
# 3. Paquetes apt (solo los que existan en los repos actuales)
# ----------------------------------------------------------------------
step "Paquetes apt instalados manualmente"
if [[ -s "$PK/apt-manual.txt" ]]; then
  apt_update
  # Paquetes elegidos + paquetes de los temas/íconos/cursores/SDDM que marcaste
  mapfile -t want < <(cat "$PK/apt-manual.txt" "$PK/theme-packages.txt" 2>/dev/null | sort -u)
  todo=(); notfound=()
  for p in "${want[@]}"; do
    [[ -z "$p" ]] && continue
    is_installed "$p" && continue
    if apt-cache show "$p" >/dev/null 2>&1; then todo+=("$p"); else notfound+=("$p"); fi
  done
  echo "  ${#want[@]} en la lista · ${#todo[@]} por instalar · ${#notfound[@]} no están en tus repos"
  [[ ${#notfound[@]} -gt 0 ]] && warn "No disponibles (de terceros o renombrados): ${notfound[*]}"
  if [[ ${#todo[@]} -gt 0 ]] && ask "Instalar ${#todo[@]} paquetes"; then
    doit sudo apt-get install -y "${todo[@]}" || {
      warn "Falló la instalación en bloque; reintentando uno por uno…"
      for p in "${todo[@]}"; do doit sudo apt-get install -y "$p" >/dev/null 2>&1 && ok "$p" || err "$p"; done
    }
  fi
else
  warn "Sin lista de paquetes apt."
fi

# ----------------------------------------------------------------------
# 4. .deb sueltos (Zoom y los que hayas respaldado)
# ----------------------------------------------------------------------
step "Programas .deb (con revisión de dependencias)"
if [[ "$(app_source Zoom)" == "deb_url" || "$(app_source Zoom)" == "apt" ]] && ! is_installed zoom; then
  if ask "Descargar la última versión de Zoom e instalarla"; then
    download_deb "https://zoom.us/client/latest/zoom_amd64.deb" "$TMP/zoom_amd64.deb" \
      && { [[ $DRY -eq 1 ]] || install_deb_with_deps "$TMP/zoom_amd64.deb"; }
  fi
fi
if compgen -G "$HERE/debs/*.deb" >/dev/null; then
  for deb in "$HERE"/debs/*.deb; do
    pkg="$(dpkg-deb -f "$deb" Package 2>/dev/null)"
    if is_installed "$pkg"; then ok "$pkg ya instalado"; continue; fi
    ask "Instalar $(basename "$deb")" && { [[ $DRY -eq 1 ]] && echo "    [dry-run] $deb" || install_deb_with_deps "$deb"; }
  done
fi

# ----------------------------------------------------------------------
# 5. Flatpak + Flathub
# ----------------------------------------------------------------------
step "Flatpak / Flathub"
if [[ -s "$PK/flatpak.txt" ]] && ask "Instalar Flatpak, Flathub y $(wc -l < "$PK/flatpak.txt") apps"; then
  command -v flatpak >/dev/null || { apt_update; doit sudo apt-get install -y flatpak; }
  is_installed gnome-software && doit sudo apt-get install -y gnome-software-plugin-flatpak >/dev/null 2>&1
  doit flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
  if [[ -s "$PK/flatpak-remotes.txt" ]]; then
    while IFS=$'\t' read -r rname rurl; do
      [[ "$rname" == "flathub" || -z "$rurl" ]] && continue
      doit flatpak remote-add --user --if-not-exists "$rname" "$rurl" 2>/dev/null || true
    done < "$PK/flatpak-remotes.txt"
  fi
  while IFS=$'\t' read -r app origin; do
    [[ -z "$app" ]] && continue
    if flatpak info "$app" >/dev/null 2>&1; then ok "$app ya instalado"; continue; fi
    doit flatpak install --user -y --noninteractive "${origin:-flathub}" "$app" >/dev/null 2>&1 \
      && ok "$app" || err "$app"
  done < "$PK/flatpak.txt"
fi

# ----------------------------------------------------------------------
# 6. Snaps
# ----------------------------------------------------------------------
step "Snaps"
if [[ -s "$PK/snap.txt" ]] && command -v snap >/dev/null && ask "Instalar $(wc -l < "$PK/snap.txt") snaps"; then
  while IFS=$'\t' read -r name chan classic; do
    [[ -z "$name" ]] && continue
    snap list "$name" >/dev/null 2>&1 && { ok "$name ya instalado"; continue; }
    flags=(--channel="${chan:-latest/stable}"); [[ "$classic" == "classic" ]] && flags+=(--classic)
    doit sudo snap install "$name" "${flags[@]}" >/dev/null && ok "$name" || err "$name"
  done < "$PK/snap.txt"
fi

# ----------------------------------------------------------------------
# 7. Archivos de configuración (dotfiles, .ssh, temas, fuentes)
# ----------------------------------------------------------------------
step "Configuraciones, llaves SSH, temas y fuentes"
if [[ -d "$HERE/home" ]] && ask "Copiar tus configuraciones a $HOME (los archivos que ya existan se renombran a *.antes-de-restaurar)"; then
  if command -v rsync >/dev/null; then
    doit rsync -a --backup --suffix=.antes-de-restaurar "$HERE/home/" "$HOME/"
  else
    doit cp -a --backup=simple --suffix=.antes-de-restaurar "$HERE/home/." "$HOME/"
  fi
  if [[ -d "$HOME/.ssh" ]]; then
    doit chmod 700 "$HOME/.ssh"
    find "$HOME/.ssh" -type f ! -name '*.pub' ! -name 'known_hosts*' ! -name 'config' \
      -exec chmod 600 {} + 2>/dev/null
    find "$HOME/.ssh" -type f \( -name '*.pub' -o -name 'known_hosts*' -o -name 'config' \) \
      -exec chmod 644 {} + 2>/dev/null
    ok "permisos de ~/.ssh corregidos (700 / 600)"
  fi
  [[ -d "$HOME/.gnupg" ]] && { chmod 700 "$HOME/.gnupg"; ok "permisos de ~/.gnupg corregidos"; }
  command -v fc-cache >/dev/null && fc-cache -f >/dev/null 2>&1 && ok "caché de fuentes actualizado"
  ok "configuraciones copiadas"
fi
# ----------------------------------------------------------------------
# Archivos fuera de tu home: temas SDDM, temas/fuentes de /usr/share, apps de /opt
# ----------------------------------------------------------------------
step "Archivos del sistema (temas SDDM, /usr/share, /opt, rutas fuera de home)"
if [[ -d "$HERE/rootfs" ]]; then
  echo "  Contiene:"; (cd "$HERE/rootfs" && find . -mindepth 3 -maxdepth 4 -type d | sed 's|^\.|    |' | head -40)
  if ask "Copiarlos a / con sudo (lo que ya exista se renombra a *.antes-de-restaurar)"; then
    if command -v rsync >/dev/null; then
      # root como dueño; lectura para todos; conserva los bits de ejecución
      doit sudo rsync -a --no-owner --no-group --omit-dir-times --chmod=Dgo+rx,Fgo+r \
        --backup --suffix=.antes-de-restaurar "$HERE/rootfs/" /
    else
      doit sudo cp -r --backup=simple --suffix=.antes-de-restaurar "$HERE/rootfs/." /
    fi
    [[ $DRY -eq 0 ]] && command -v fc-cache >/dev/null && sudo fc-cache -f >/dev/null 2>&1
    ok "archivos del sistema restaurados"
  fi
else
  ok "nada que copiar fuera de tu home"
fi
if [[ -d "$PK/sddm" ]]; then
  cur="$(grep -rh '^Current=' "$PK/sddm" 2>/dev/null | tail -1 | cut -d= -f2)"
  if [[ -n "$cur" ]] && ask "Poner «$cur» como tema de la pantalla de inicio (SDDM)"; then
    is_installed sddm || warn "SDDM no está instalado; se deja la configuración lista por si lo instalas"
    [[ -f "$PK/sddm/sddm.conf" ]] && doit sudo cp --backup=simple --suffix=.antes-de-restaurar "$PK/sddm/sddm.conf" /etc/sddm.conf
    if [[ -d "$PK/sddm/conf.d" ]]; then
      doit sudo mkdir -p /etc/sddm.conf.d
      doit sudo cp --backup=simple --suffix=.antes-de-restaurar "$PK/sddm/conf.d/"*.conf /etc/sddm.conf.d/
    fi
    [[ -d "/usr/share/sddm/themes/$cur" ]] && ok "tema SDDM: $cur" || warn "no encuentro /usr/share/sddm/themes/$cur (¿lo marcaste en Temas?)"
  fi
fi
if [[ -s "$PK/manual-apps.txt" ]]; then
  ok "apps manuales restauradas con tus archivos: $(cut -f1 "$PK/manual-apps.txt" | paste -sd, | sed 's/,/, /g')"
fi

if [[ -d "$HERE/secrets" ]]; then
  warn "Tus contraseñas exportadas están en: $HERE/secrets"
  warn "Impórtalas en Brave (brave://password-manager/settings) y BORRA el CSV después: shred -u archivo.csv"
fi

# ----------------------------------------------------------------------
# 8. Zsh + Oh My Zsh + Powerlevel10k + VS Code extensiones
# ----------------------------------------------------------------------
step "Terminal (zsh, Oh My Zsh, Powerlevel10k) y extensiones de VS Code"
has_omz=0; [[ -n "$(app_source 'Oh My Zsh')" ]] && has_omz=1
p10k_dir="$(awk -F'\t' '$1=="Powerlevel10k"{print $3}' "$PK/apps.txt" 2>/dev/null)"
if [[ -n "$p10k_dir$(app_source Zsh)" ]] && ask "Configurar zsh con Oh My Zsh / Powerlevel10k"; then
  is_installed zsh || { apt_update; doit sudo apt-get install -y zsh; }
  if [[ $has_omz -eq 1 && ! -f "$HOME/.oh-my-zsh/oh-my-zsh.sh" ]]; then
    # Instala Oh My Zsh sin tocar el .zshrc restaurado
    tmpomz="$HOME/.oh-my-zsh.custom-restaurado"
    [[ -d "$HOME/.oh-my-zsh/custom" ]] && doit mv "$HOME/.oh-my-zsh/custom" "$tmpomz"
    [[ -d "$HOME/.oh-my-zsh" ]] && doit rm -rf "$HOME/.oh-my-zsh"
    doit env RUNZSH=no CHSH=no KEEP_ZSHRC=yes sh -c \
      "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended
    [[ -d "$tmpomz" ]] && { doit cp -a "$tmpomz/." "$HOME/.oh-my-zsh/custom/"; doit rm -rf "$tmpomz"; }
    ok "Oh My Zsh"
  fi
  if [[ -n "$p10k_dir" && ! -f "$HOME/$p10k_dir/powerlevel10k.zsh-theme" ]]; then
    doit rm -rf "${HOME:?}/$p10k_dir"
    doit git clone --depth=1 https://github.com/romkatv/powerlevel10k.git "$HOME/$p10k_dir" && ok "Powerlevel10k en ~/$p10k_dir"
  fi
  # Fuente recomendada por p10k (si no venía en el respaldo)
  if ! fc-list 2>/dev/null | grep -qi "MesloLGS NF"; then
    mkdir -p "$HOME/.local/share/fonts"
    for s in Regular Bold Italic "Bold Italic"; do
      doit curl -fsSL -o "$HOME/.local/share/fonts/MesloLGS NF $s.ttf" \
        "https://github.com/romkatv/powerlevel10k-media/raw/master/MesloLGS%20NF%20${s// /%20}.ttf"
    done
    fc-cache -f >/dev/null 2>&1; ok "fuente MesloLGS NF"
  fi
  if grep -q '^SHELL=zsh' "$PK/shell.txt" 2>/dev/null && [[ "$(basename "$SHELL")" != "zsh" ]]; then
    doit chsh -s "$(command -v zsh)" && ok "zsh será tu shell al volver a iniciar sesión"
  fi
fi
if [[ -s "$PK/vscode-extensions.txt" ]]; then
  CODE="$(command -v code || command -v codium || true)"
  if [[ -n "$CODE" ]] && ask "Instalar $(grep -c . "$PK/vscode-extensions.txt") extensiones de VS Code"; then
    while read -r ext; do
      [[ -z "$ext" ]] && continue
      doit "$CODE" --install-extension "$ext" --force >/dev/null 2>&1 && ok "$ext" || err "$ext"
    done < "$PK/vscode-extensions.txt"
  fi
fi

# ----------------------------------------------------------------------
# 9. Tema del escritorio
# ----------------------------------------------------------------------
step "Tema del escritorio"
if [[ -s "$PK/dconf.ini" ]] && command -v dconf >/dev/null; then
  if ask "Cargar TODOS los ajustes del escritorio (dconf: atajos, dock, extensiones)"; then
    doit dconf load / < "$PK/dconf.ini" && ok "dconf cargado"
  fi
elif [[ -s "$PK/themes.txt" ]] && command -v gsettings >/dev/null; then
  if ask "Aplicar tema, íconos, cursor y fuentes"; then
    while IFS=$'\t' read -r schema key value; do
      [[ -z "$schema" ]] && continue
      doit gsettings set "$schema" "$key" "$value" 2>/dev/null && ok "$key = $value"
    done < "$PK/themes.txt"
  fi
fi

echo
say "¡Restauración terminada! Cierra sesión y vuelve a entrar para ver todo aplicado."
