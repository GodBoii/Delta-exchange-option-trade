$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$candidatePorts = @(8000, 8585, 8085, 8011)
$binancePort = if ($env:BINANCE_PORT -match '^\d{2,5}$') { [int]$env:BINANCE_PORT } else { 8001 }
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
            $candidatePorts = @($configuredPorts | Where-Object { $_ -ne $binancePort })
        }
    }
}

$composeFile = Join-Path $projectRoot "docker-compose.yml"
$backendIsRunning = (docker compose -f $composeFile ps --status running --services 2>$null) -contains "delta-exchange"
$currentBinding = if ($backendIsRunning) {
    docker compose -f $composeFile port delta-exchange 8000 2>$null | Select-Object -First 1
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
        docker compose logs --tail 50 delta-exchange
        throw "The backend started but did not become healthy on port $backendPort."
    }

    $binanceHealthy = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $marketHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$binancePort/health" -TimeoutSec 2
            $streamReady = $marketHealth.realtime.connected -and
                $marketHealth.realtime.bookSynced -and
                $null -ne $marketHealth.realtime.eventAgeMs -and
                $marketHealth.realtime.eventAgeMs -lt 30000
            if ($marketHealth.success -and
                $marketHealth.service -eq "binance-market-data-api" -and
                $streamReady) {
                $binanceHealthy = $true
                break
            }
        }
        catch {}
        Start-Sleep -Milliseconds 500
    }

    if (-not $binanceHealthy) {
        docker compose logs --tail 50 binace
        throw "The Binace market-data service did not become healthy on port $binancePort."
    }

    Write-Host "Delta backend is healthy: http://localhost:$backendPort"
    Write-Host "API documentation: http://localhost:$backendPort/docs"
    Write-Host "Binance BTCUSDT Spot stream is healthy: http://localhost:$binancePort"
    Write-Host "Market API documentation: http://localhost:$binancePort/docs"
}
finally {
    Pop-Location
}
