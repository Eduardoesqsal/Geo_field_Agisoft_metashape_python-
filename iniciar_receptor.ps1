param([switch]$ValidateOnly)

$ErrorActionPreference = 'Stop'
$configPath = Join-Path $PSScriptRoot '.env.receiver.local'

if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw 'Falta .env.receiver.local en la raiz del proyecto.'
}

$allowedKeys = @(
    'GEOFIELD_API_BASE_URL',
    'GEOFIELD_SUPABASE_URL',
    'GEOFIELD_SUPABASE_ANON_KEY',
    'GEOFIELD_WORKER_EMAIL',
    'GEOFIELD_WORKER_PASSWORD',
    'GEOFIELD_ACCESS_TOKEN',
    'GEOFIELD_POLL_SECONDS'
)

foreach ($line in Get-Content -LiteralPath $configPath -Encoding UTF8) {
    $entry = $line.Trim()
    if (-not $entry -or $entry.StartsWith('#')) { continue }

    $separator = $entry.IndexOf('=')
    if ($separator -lt 1) { throw 'Hay una linea invalida en .env.receiver.local.' }
    $key = $entry.Substring(0, $separator).Trim()
    $value = $entry.Substring($separator + 1)
    if ($key -notin $allowedKeys) { throw "Variable no permitida: $key" }
    [Environment]::SetEnvironmentVariable($key, $value, 'Process')
}

if (-not $env:GEOFIELD_API_BASE_URL) {
    throw 'Completa GEOFIELD_API_BASE_URL antes de iniciar el receptor.'
}

if (-not $env:GEOFIELD_ACCESS_TOKEN) {
    $requiredKeys = @(
        'GEOFIELD_SUPABASE_URL',
        'GEOFIELD_SUPABASE_ANON_KEY',
        'GEOFIELD_WORKER_EMAIL',
        'GEOFIELD_WORKER_PASSWORD'
    )
    foreach ($key in $requiredKeys) {
        if (-not [Environment]::GetEnvironmentVariable($key, 'Process')) {
            throw "Completa $key o usa GEOFIELD_ACCESS_TOKEN para una prueba temporal."
        }
    }
}

if ($ValidateOnly) {
    Write-Output 'Configuracion del receptor completa. No se inicio el servidor.'
    return
}

Push-Location $PSScriptRoot
try {
    python app.py
} finally {
    Pop-Location
}
