$env:BH_HOME = Join-Path $PSScriptRoot 'state'
& (Join-Path $PSScriptRoot '.venv\Scripts\browser-harness.exe') @args
exit $LASTEXITCODE
