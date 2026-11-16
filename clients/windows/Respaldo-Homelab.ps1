<#
.SYNOPSIS
  Respaldo de Windows hacia MyHomeLab (la Raspberry lo guarda y lo reenvía a tu bot).

.DESCRIPTION
  1. Exporta la lista de programas (winget) para reinstalarlos con un comando.
  2. Copia tus carpetas y configuraciones (ver homelab-backup.json).
  3. Comprime todo en .7z cifrado (AES-256, compresión máxima) en partes de 1.9 GB.
  4. Sube cada parte a https://TU-PI/api/backup/<dispositivo>/... con curl.exe.

.EXAMPLE
  .\Respaldo-Homelab.ps1 -Configurar     # primera vez: crea homelab-backup.json y guarda la contraseña
  .\Respaldo-Homelab.ps1                 # hace el respaldo
  .\Respaldo-Homelab.ps1 -Programar      # tarea programada cada 12 semanas
  .\Respaldo-Homelab.ps1 -SoloLocal      # no sube nada, solo deja el .7z
#>
[CmdletBinding()]
param(
    [switch]$Configurar,
    [switch]$Programar,
    [switch]$SoloLocal
)
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigFile = Join-Path $Here "homelab-backup.json"
$SecretFile = Join-Path $env:LOCALAPPDATA "MyHomeLab\backup-secret.xml"

function Say($m, $c = "Cyan") { Write-Host "==> $m" -ForegroundColor $c }

# ---------------------------------------------------------------- configuración
if ($Configurar -or -not (Test-Path $ConfigFile)) {
    Say "Configuración inicial"
    $cfg = [ordered]@{
        url       = Read-Host "URL del homelab (ej. https://192.168.0.5)"
        device    = Read-Host "Nombre del dispositivo en homelab.yml (ej. laptop-win)"
        ca        = ""   # ruta al root.crt de Caddy; vacío = no verificar certificado
        destino   = (Join-Path $env:USERPROFILE "Respaldos")
        carpetas  = @(
            "$env:USERPROFILE\Documents",
            "$env:USERPROFILE\Desktop",
            "$env:USERPROFILE\.ssh",
            "$env:USERPROFILE\.gitconfig",
            "$env:APPDATA\Code\User\settings.json",
            "$env:APPDATA\Code\User\keybindings.json",
            "$env:APPDATA\Code\User\snippets",
            "$env:LOCALAPPDATA\Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json",
            "$env:USERPROFILE\Documents\PowerShell\Microsoft.PowerShell_profile.ps1",
            "$env:LOCALAPPDATA\Microsoft\Windows\Fonts"
        )
        excluir   = @("node_modules", ".cache", "*.tmp", "Cache", "GPUCache")
    }
    $cfg | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 $ConfigFile
    New-Item -ItemType Directory -Force (Split-Path $SecretFile) | Out-Null
    $tok = Read-Host "Token del dispositivo (BACKUP_TOKEN_... del .env de la Pi)" -AsSecureString
    $pw = Read-Host "Contraseña para cifrar el .7z (¡no la olvides!)" -AsSecureString
    # Export-Clixml cifra con DPAPI: solo tu usuario en esta PC puede leerlo
    @{ token = $tok; password = $pw } | Export-Clixml $SecretFile
    Say "Guardado en $ConfigFile (edita 'carpetas' para elegir qué respaldar)" "Green"
    if ($Configurar) { exit 0 }
}

$cfg = Get-Content $ConfigFile -Raw | ConvertFrom-Json
$secret = Import-Clixml $SecretFile
$plain = { param($s) [Runtime.InteropServices.Marshal]::PtrToStringAuto(
           [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)) }

# ---------------------------------------------------------- tarea programada
if ($Programar) {
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`""
    $trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 12 -DaysOfWeek Sunday -At 11am
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable
    Register-ScheduledTask -TaskName "MyHomeLab Respaldo" -Action $action -Trigger $trigger `
        -Settings $settings -Description "Respaldo cada 12 semanas a MyHomeLab" -Force | Out-Null
    Say "Tarea «MyHomeLab Respaldo» creada: cada 12 semanas, domingo 11:00" "Green"
    exit 0
}

# ------------------------------------------------------------------- 7-Zip
$SevenZip = @("$env:ProgramFiles\7-Zip\7z.exe", "${env:ProgramFiles(x86)}\7-Zip\7z.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $SevenZip) {
    Say "Instalando 7-Zip con winget…" "Yellow"
    winget install --id 7zip.7zip -e --accept-source-agreements --accept-package-agreements
    $SevenZip = "$env:ProgramFiles\7-Zip\7z.exe"
}

$stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
$name = "respaldo_$($cfg.device)_$stamp"
$work = Join-Path $env:TEMP $name
New-Item -ItemType Directory -Force $work, "$work\archivos", $cfg.destino | Out-Null

# ---------------------------------------------------------- lista de programas
Say "Exportando lista de programas (winget)…"
try {
    winget export -o "$work\apps-winget.json" --include-versions --accept-source-agreements | Out-Null
} catch { Write-Warning "winget export falló: $_" }
Get-ItemProperty HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*,
                 HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*,
                 HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\* -ErrorAction SilentlyContinue |
    Where-Object DisplayName | Select-Object DisplayName, DisplayVersion, Publisher |
    Sort-Object DisplayName | Export-Csv -NoTypeInformation -Encoding UTF8 "$work\programas-instalados.csv"
@"
# Restaurar en un Windows nuevo:
#   1) Extrae: 7z x $name.7z.001
#   2) PowerShell:  winget import -i apps-winget.json --accept-package-agreements --accept-source-agreements
#   3) Copia de archivos\ cada carpeta a su lugar (la ruta original está en rutas.txt)
"@ | Set-Content -Encoding UTF8 "$work\LEEME.txt"

# ---------------------------------------------------------------- archivos
Say "Copiando carpetas y configuraciones…"
$map = @()
$i = 0
foreach ($src in $cfg.carpetas) {
    $src = [Environment]::ExpandEnvironmentVariables($src)
    if (-not (Test-Path $src)) { continue }
    $i++
    $dst = Join-Path "$work\archivos" ("{0:D2}_{1}" -f $i, (Split-Path $src -Leaf))
    if ((Get-Item $src).PSIsContainer) {
        robocopy $src $dst /E /R:1 /W:1 /NFL /NDL /NJH /NJS /XD $cfg.excluir /XF $cfg.excluir | Out-Null
    } else {
        Copy-Item $src $dst -Force
    }
    $map += "$(Split-Path $dst -Leaf)`t$src"
    Write-Host "   $src"
}
$map | Set-Content -Encoding UTF8 "$work\rutas.txt"

# --------------------------------------------------------------- compresión
Say "Comprimiendo (7z, -mx=9, AES-256, partes de 1.9 GB)…"
$archive = Join-Path $cfg.destino "$name.7z"
$pw = & $plain $secret.password
& $SevenZip a -t7z -mx=9 -mhe=on "-p$pw" -v1900m $archive "$work\*" -bsp1 | Out-Host
$pw = $null
if ($LASTEXITCODE -ne 0) { throw "7-Zip falló ($LASTEXITCODE)" }
Remove-Item -Recurse -Force $work
$parts = Get-ChildItem "$archive.*" | Sort-Object Name
Say "Listo: $($parts.Count) parte(s) en $($cfg.destino)" "Green"

if ($SoloLocal) { exit 0 }

# ------------------------------------------------------------------- subida
$session = Get-Date -Format "yyyyMMddHHmmss"
$tok = & $plain $secret.token
$curlConf = Join-Path $env:TEMP "homelab-curl-$session.txt"
foreach ($p in $parts) {
    Say "Subiendo $($p.Name) ($([math]::Round($p.Length / 1MB)) MB)…"
    $url = "$($cfg.url.TrimEnd('/'))/api/backup/$($cfg.device)/$([uri]::EscapeDataString($p.Name))"
    # El token va en un archivo temporal de configuración de curl (no en la línea de comandos)
    @("header = `"Authorization: Bearer $tok`"", "header = `"X-Backup-Session: $session`"",
      $(if ($cfg.ca) { "cacert = `"$($cfg.ca -replace '\\', '/')`"" } else { "insecure" })) |
        Set-Content -Encoding ASCII $curlConf
    $code = & curl.exe -sS -K $curlConf -T $p.FullName -o NUL -w "%{http_code}" $url
    Remove-Item $curlConf -Force
    if ($code -ne "200") { throw "El homelab respondió $code al subir $($p.Name)" }
    Write-Host "   ✔ guardado en la Pi" -ForegroundColor Green
}
$tok = $null
Say "Respaldo enviado a MyHomeLab ✅ (llegará también a tu Telegram)" "Green"
