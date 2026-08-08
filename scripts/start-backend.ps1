$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$candidatePorts = @(8000, 8585, 8085, 8011, 8001)
$envFile = Join-Path $projectRoot ".env.local"

if (Test-Path -LiteralPath $envFile) {
    $portsLine = Get-Content -LiteralPath $envFile |
        Where-Object { $_ -match '^NEXT_PUBLIC_API_PORTS=' } |
        Select-Object -First 1

    if ($portsLine) {
        $configuredPorts = ($portsLine -replace '^NEXT_PUBLIC_API_PORTS=', '').Split(',') |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ -match '^\d{2,5}$' } |
            ForEach-Object { [int]$_ }

        if ($configuredPorts.Count -gt 0) {
            $candidatePorts = @($configuredPorts)
        }
    }
}

$composeFile = Join-Path $projectRoot "docker-compose.yml"
$backendIsRunning = (docker compose -f $composeFile ps --status running --services 2>$null) -contains "backend"
$currentBinding = if ($backendIsRunning) {
    docker compose -f $composeFile port backend 8000 2>$null | Select-Object -First 1
}
else {
    $null
}
$currentPort = if ($currentBinding -match ':(\d+)$') { [int]$Matches[1] } else { $null }

if ($currentPort -and $currentPort -in $candidatePorts) {
    $backendPort = $currentPort
}
else {
    $listeningPorts = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty LocalPort -Unique)
    $backendPort = $candidatePorts |
        Where-Object { $_ -notin $listeningPorts } |
        Select-Object -First 1
}

if (-not $backendPort) {
    throw "No backend port is available. Checked: $($candidatePorts -join ', ')."
}

$env:BACKEND_PORT = [string]$backendPort
Write-Host "Starting the Delta backend on http://localhost:$backendPort ..."

Push-Location $projectRoot
try {
    # Recreate the service so containers retained across a Docker Desktop restart
    # cannot keep stale DNS resolver state.
    docker compose up -d --build --force-recreate
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose could not start the backend."
    }

    $healthy = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$backendPort/health" -TimeoutSec 2
            $schedulerReady = -not $health.scheduler.enabled -or (
                $health.scheduler.running -and
                $health.scheduler.lastCompletedAt -and
                -not $health.scheduler.lastError
            )
            if ($health.success -and $health.service -eq "delta-strategy-api" -and $schedulerReady) {
                $healthy = $true
                break
            }
        }
        catch {}
        Start-Sleep -Milliseconds 500
    }

    if (-not $healthy) {
        docker compose logs --tail 50 backend
        throw "The backend started but did not become healthy on port $backendPort."
    }

    Write-Host "Delta backend is healthy: http://localhost:$backendPort"
    Write-Host "API documentation: http://localhost:$backendPort/docs"
}
finally {
    Pop-Location
}
