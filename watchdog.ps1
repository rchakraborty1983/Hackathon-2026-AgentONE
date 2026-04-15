<#
.SYNOPSIS
    AgentONE Watchdog â€” auto-restarts TFS MCP (port 9000), AgentONE (port 9100), and the dev tunnel.
.DESCRIPTION
    Run this in a dedicated terminal. It checks every 10 seconds whether the servers
    are listening on their ports and the dev tunnel is hosting. If any is down, it restarts.
    Press Ctrl+C to stop.
.USAGE
    powershell -ExecutionPolicy Bypass -File C:\TFS_MCP\watchdog.ps1
#>

$ErrorActionPreference = "SilentlyContinue"
$python = "C:\TFS_MCP\.venv\Scripts\python.exe"
$tunnelName = "agentone"
$tunnelUrl = "https://px2wkg66-9100.use.devtunnels.ms"
$checkInterval = 10  # seconds

function Test-Port([int]$port) {
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $tcp.Connect("127.0.0.1", $port)
        $tcp.Close()
        return $true
    } catch {
        return $false
    }
}

function Start-TfsMcp {
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Starting TFS MCP on port 9000..." -ForegroundColor Yellow
    $proc = Start-Process -FilePath $python -ArgumentList "TFSMCP.py" `
        -WorkingDirectory "C:\TFS_MCP" -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput "C:\TFS_MCP\logs\tfsmcp_stdout.log" `
        -RedirectStandardError "C:\TFS_MCP\logs\tfsmcp_stderr.log"
    # Wait up to 8 seconds for it to bind
    for ($i = 0; $i -lt 16; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-Port 9000) {
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] TFS MCP is UP (PID $($proc.Id))" -ForegroundColor Green
            return $proc
        }
    }
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] TFS MCP failed to start!" -ForegroundColor Red
    return $null
}

function Start-AgentOne {
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Starting AgentONE on port 9100..." -ForegroundColor Yellow
    $proc = Start-Process -FilePath $python -ArgumentList "-m","uvicorn","app:app","--host","0.0.0.0","--port","9100","--no-access-log" `
        -WorkingDirectory "C:\TFS_MCP\teams_bot" -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput "C:\TFS_MCP\logs\agentone_stdout.log" `
        -RedirectStandardError "C:\TFS_MCP\logs\agentone_stderr.log"
    # Wait up to 10 seconds for it to bind
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-Port 9100) {
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] AgentONE is UP (PID $($proc.Id))" -ForegroundColor Green
            return $proc
        }
    }
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] AgentONE failed to start!" -ForegroundColor Red
    return $null
}

function Test-DevTunnel {
    # Check if devtunnel host process is running
    $dtProc = Get-Process devtunnel -ErrorAction SilentlyContinue
    return ($null -ne $dtProc)
}

function Start-DevTunnel {
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Starting dev tunnel ($tunnelName)..." -ForegroundColor Yellow
    # Ensure port is registered
    & devtunnel port create $tunnelName -p 9100 2>$null
    $proc = Start-Process -FilePath "devtunnel" -ArgumentList "host",$tunnelName `
        -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput "C:\TFS_MCP\logs\devtunnel_stdout.log" `
        -RedirectStandardError "C:\TFS_MCP\logs\devtunnel_stderr.log"
    Start-Sleep -Seconds 3
    if (Test-DevTunnel) {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Dev tunnel is UP (PID $($proc.Id)) -> $tunnelUrl" -ForegroundColor Green
        return $proc
    }
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Dev tunnel failed to start!" -ForegroundColor Red
    return $null
}

# Create logs directory
New-Item -ItemType Directory -Path "C:\TFS_MCP\logs" -Force | Out-Null

Write-Host "======================================" -ForegroundColor Cyan
Write-Host "  AgentONE Watchdog" -ForegroundColor Cyan
Write-Host "  Checking every ${checkInterval}s" -ForegroundColor Cyan
Write-Host "  Tunnel: $tunnelUrl" -ForegroundColor Cyan
Write-Host "  Press Ctrl+C to stop" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan

# Initial startup â€” only start what's not already running
if (-not (Test-Port 9000)) { Start-TfsMcp }
else { Write-Host "[$(Get-Date -Format 'HH:mm:ss')] TFS MCP already running on 9000" -ForegroundColor Green }

if (-not (Test-Port 9100)) { Start-AgentOne }
else { Write-Host "[$(Get-Date -Format 'HH:mm:ss')] AgentONE already running on 9100" -ForegroundColor Green }

if (-not (Test-DevTunnel)) { Start-DevTunnel }
else { Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Dev tunnel already running" -ForegroundColor Green }

# Monitoring loop
$lastOk = Get-Date
while ($true) {
    Start-Sleep -Seconds $checkInterval

    $mcp = Test-Port 9000
    $agent = Test-Port 9100
    $tunnel = Test-DevTunnel

    if ($mcp -and $agent -and $tunnel) {
        $lastOk = Get-Date
        continue
    }

    if (-not $mcp) {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] TFS MCP (9000) is DOWN â€” restarting..." -ForegroundColor Red
        # Kill any zombie process on port 9000
        $pids = netstat -ano | Select-String ":9000\s" | ForEach-Object {
            ($_ -split '\s+')[-1]
        } | Where-Object { $_ -match '^\d+$' } | Sort-Object -Unique
        foreach ($p in $pids) {
            Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 1
        Start-TfsMcp
    }

    if (-not $agent) {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] AgentONE (9100) is DOWN â€” restarting..." -ForegroundColor Red
        # Kill any zombie process on port 9100
        $pids = netstat -ano | Select-String ":9100\s" | ForEach-Object {
            ($_ -split '\s+')[-1]
        } | Where-Object { $_ -match '^\d+$' } | Sort-Object -Unique
        foreach ($p in $pids) {
            Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 1
        Start-AgentOne
    }

    if (-not $tunnel) {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Dev tunnel is DOWN - restarting..." -ForegroundColor Red
        Get-Process devtunnel -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
        Start-DevTunnel
    }
}
