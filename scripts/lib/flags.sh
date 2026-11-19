# shellcheck shell=bash
# Las opciones de los scripts ahora están en inglés (--yes, --install, --remove…).
# Esto acepta también las de antes en español, para que nada de lo que ya tienes deje de servir.
#   . "$(dirname "$(readlink -f "$0")")/lib/flags.sh"; hl_flags "$@"; set -- "${HL_ARGS[@]}"
declare -A HL_FLAG_ES=(
  [--si]=--yes [--forzar]=--force [--actualizar]=--update [--estado]=--status [--diagnostico]=--diagnose
  [--diagnóstico]=--diagnose [--abrir-firewall]=--open-firewall [--solo-ml]=--ml-only [--ml-remoto]=--ml-remote
  [--traer-de]=--pull-from [--exportar]=--export [--fin-exportar]=--export-done [--cliente]=--client
  [--permitir]=--allow [--apagar-en]=--shutdown-after [--probar]=--test [--dominio]=--domain [--sin-2fa]=--no-2fa
  [--quitar]=--remove [--quitar-docker]=--remove-docker [--instalar]=--install [--puerto]=--port
  [--puerto-ssh]=--ssh-port [--sin-respaldo]=--no-backup [--borrar-datos]=--delete-data [--desde-cero]=--from-scratch
  [--desde-pi]=--from-pi [--sin-firewall-pi]=--no-pi-firewall [--con-respaldos]=--with-backups [--con-tunel]=--with-tunnel
  [--sin-pc]=--no-pc [--limpiar-pi]=--clean-pi [--copia-para]=--copy-for [--solo-llave]=--key-only [--arreglar]=--fix
  [--ayuda]=--help
)
hl_flags() {
  HL_ARGS=()
  local a
  for a in "$@"; do HL_ARGS+=("${HL_FLAG_ES[$a]:-$a}"); done
}
