@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0tunnel-profile.yaml" (
  echo Missing tunnel profile:
  echo   %~dp0tunnel-profile.yaml
  echo Copy tunnel-profile.example.yaml to tunnel-profile.yaml and fill in the placeholders.
  pause
  exit /b 2
)
if not exist "%~dp0secrets\openai-tunnel-key.txt" (
  echo Missing Computer Use Runtime Key file:
  echo   %~dp0secrets\openai-tunnel-key.txt
  pause
  exit /b 2
)
start "" /b /belownormal /wait "%~dp0tunnel-client-v0.0.14-windows-amd64\tunnel-client.exe" run --profile-file "%~dp0tunnel-profile.yaml"
pause
