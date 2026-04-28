@echo off
title AgentONE - OnBase DevOps Assistant
echo ============================================
echo   AgentONE - OnBase DevOps Assistant
echo ============================================
echo.

REM Activate the Python virtual environment
call C:\TFS_MCP\.venv\Scripts\activate.bat

REM Require AGENTONE_API_KEY to be supplied via the user environment.
REM Set it once with:
REM   [System.Environment]::SetEnvironmentVariable("AGENTONE_API_KEY","<your-secret>","User")
REM Never hardcode the value here -- this file is committed to a public repo.
if not defined AGENTONE_API_KEY (
    echo ERROR: AGENTONE_API_KEY environment variable is not set.
    echo Set it as a User env var, then restart this shell.
    exit /b 1
)

REM Check if openai is installed
python -c "import openai" 2>nul
if errorlevel 1 (
    echo Installing openai package...
    pip install openai -q
)

echo Starting AgentONE API on port 9100...
echo API Key will be printed in the console output.
echo.
cd /d C:\TFS_MCP\teams_bot
python app.py
