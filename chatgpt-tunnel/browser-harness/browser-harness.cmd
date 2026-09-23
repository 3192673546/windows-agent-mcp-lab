@echo off
set "BH_HOME=%~dp0state"
"%~dp0.venv\Scripts\browser-harness.exe" %*
