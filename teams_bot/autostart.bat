@echo off
title AgentONE Services
echo ============================================
echo   AgentONE - Auto-Start Services
echo ============================================
echo.

REM Refresh PATH to include devtunnel
set PATH=%PATH%;%LOCALAPPDATA%\Microsoft\DevTunnels

REM Load API key from user environment
for /f "tokens=*" %%i in ('powershell -NoProfile -Command "[System.Environment]::GetEnvironmentVariable('AGENTONE_API_KEY','User')"') do set AGENTONE_API_KEY=%%i

REM Start dev tunnel in background
echo Starting Dev Tunnel...
start "DevTunnel" /MIN cmd /c "devtunnel host agentone"
timeout /t 5 /nobreak >nul

REM Start AgentONE API server
echo Starting AgentONE API server on port 9100...
cd /d C:\TFS_MCP\teams_bot
C:\TFS_MCP\.venv\Scripts\python.exe app.py
