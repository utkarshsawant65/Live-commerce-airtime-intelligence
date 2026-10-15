# load_env.ps1 — load .env into the current PowerShell session.
# Usage (dot-source so the vars stick in THIS terminal):
#     . .\load_env.ps1
# Run once per new terminal, BEFORE any dbt command.

$envFile = Join-Path $PSScriptRoot ".env"
if (-not (Test-Path $envFile)) {
    Write-Host "No .env found at $envFile" -ForegroundColor Red
    return
}

Get-Content $envFile | Where-Object { $_ -match '^\s*[^#].*=' } | ForEach-Object {
    $name, $value = $_ -split '=', 2
    Set-Item -Path "env:$($name.Trim())" -Value $value.Trim()
}

Write-Host "Loaded .env -> SNOWFLAKE_ACCOUNT=$env:SNOWFLAKE_ACCOUNT, USER=$env:SNOWFLAKE_USER" -ForegroundColor Green
