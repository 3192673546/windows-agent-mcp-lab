@echo off
setlocal
cd /d "%~dp0"
"%~dp0tunnel-client-v0.0.14-windows-amd64\tunnel-client.exe" doctor --profile-file "%~dp0tunnel-profile.yaml" --explain
pause
