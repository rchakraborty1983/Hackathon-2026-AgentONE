#Requires -Version 5.1
<#
.SYNOPSIS
    Auto-restart wrapper for the AgentONE FastAPI server.
    Monitors the process and restarts it if it crashes, with backoff.
.USAGE
    .\run_server.ps1                    # Normal mode
    .\run_server.ps1 -AsTunnel          # Also starts dev tunnel
#>
param(
    [switch]$AsTunnel
)

$ErrorActionPreference = "Continue"
$ServerDir   = "C:\TFS_MCP\teams_bot"
$PythonExe   = "C:\TFS_MCP\.venv\Scripts\python.exe"
$Port        = 9100
$MaxRestarts = 50
$BaseDelay   = 2        # seconds, doubles on consecutive fast crashes
$MaxDelay    = 60
$CrashWindow = 30       # if process runs < this many seconds, it's a "fast crash"

$TunnelId    = "agentone.use"
$tunnelProc  = $null

function Write-Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$ts] $msg" -ForegroundColor Cyan
}

function Stop-PortProcess($port) {
    Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue |
        Select-Object OwningProcess -Unique |
        ForEach-Object {
            $pid_ = $_.OwningProcess
            if ($pid_ -ne 0 -and $pid_ -ne $PID) {
                Write-Log "Killing zombie process $pid_ on port $port"
                Stop-Process -Id $pid_ -Force -ErrorAction SilentlyContinue
                Start-Sleep -Milliseconds 500
            }
        }
}

function Start-DevTunnel {
    if (-not $AsTunnel) { return }
    Write-Log "Starting dev tunnel: $TunnelId"
    $script:tunnelProc = Start-Process -FilePath "devtunnel" `
        -ArgumentList "host", $TunnelId, "--allow-anonymous" `
        -PassThru -WindowStyle Minimized
    Write-Log "Dev tunnel PID: $($script:tunnelProc.Id)"
}

# ── Main loop ──
Write-Log "AgentONE Server Watchdog starting"
Write-Log "Server dir: $ServerDir"
Write-Log "Python: $PythonExe"
Write-Log "Port: $Port"

Start-DevTunnel

$restartCount = 0
$delay = $BaseDelay

for ($i = 0; $i -lt $MaxRestarts; $i++) {
    # Kill anything on the port before starting
    Stop-PortProcess $Port

    Write-Log "Starting server (attempt $($i + 1)/$MaxRestarts)..."
    $startTime = Get-Date

    $proc = Start-Process -FilePath $PythonExe `
        -ArgumentList "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", $Port `
        -WorkingDirectory $ServerDir `
        -PassThru -NoNewWindow

    # Wait for process to exit
    $proc.WaitForExit()
    $runTime = ((Get-Date) - $startTime).TotalSeconds
    $exitCode = $proc.ExitCode

    Write-Log "Server exited with code $exitCode after $([math]::Round($runTime, 1))s"

    if ($runTime -lt $CrashWindow) {
        # Fast crash — increase delay
        $delay = [math]::Min($delay * 2, $MaxDelay)
        Write-Log "Fast crash detected. Waiting ${delay}s before restart..."
    } else {
        # Normal exit or long-running crash — reset delay
        $delay = $BaseDelay
        Write-Log "Restarting in ${delay}s..."
    }

    Start-Sleep -Seconds $delay
}

Write-Log "Max restarts ($MaxRestarts) reached. Giving up."

# Cleanup tunnel
if ($tunnelProc -and -not $tunnelProc.HasExited) {
    Write-Log "Stopping dev tunnel"
    Stop-Process -Id $tunnelProc.Id -Force -ErrorAction SilentlyContinue
}
