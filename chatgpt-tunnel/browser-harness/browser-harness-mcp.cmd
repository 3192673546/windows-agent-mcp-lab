@echo off
setlocal
set "BH_HOME=%~dp0state"
set "BH_RECORD=0"
"%~dp0.venv\Scripts\browser-harness-mcp.exe"
