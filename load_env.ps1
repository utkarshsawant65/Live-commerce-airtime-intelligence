# Load .env into the current PowerShell session. Dot-source it so the variables persist:
#     . .\load_env.ps1

$envFile = Join-Path $PSScriptRoot ".env"
if (-not (Test-Path $envFile)) {
    Write-Host "No .env found at $envFile" -ForegroundColor Red
    return
}

foreach ($line in Get-Content $envFile) {
    $line = $line.Trim()
    if ($line -eq "" -or $line.StartsWith("#") -or -not $line.Contains("=")) { continue }
    $line = $line -replace '^export\s+', ''
    $name, $value = $line -split '=', 2
    $value = $value.Trim()
    if ($value.Length -ge 2 -and ($value[0] -eq '"' -or $value[0] -eq "'") -and $value[-1] -eq $value[0]) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    Set-Item -Path "env:$($name.Trim())" -Value $value
}

Write-Host "Loaded .env -> SNOWFLAKE_ACCOUNT=$env:SNOWFLAKE_ACCOUNT, USER=$env:SNOWFLAKE_USER" -ForegroundColor Green
