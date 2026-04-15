@echo off
title AgentONE - OnBase DevOps Assistant
echo ============================================
echo   AgentONE - OnBase DevOps Assistant
echo ============================================
echo.

REM Activate the Python virtual environment
call C:\TFS_MCP\.venv\Scripts\activate.bat

REM Set a fixed API key (change this to your own secret)
if not defined AGENTONE_API_KEY (
    set AGENTONE_API_KEY=agentone-hyland-2026
    echo WARNING: Using default API key. Set AGENTONE_API_KEY env var for production.
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
