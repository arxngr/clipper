$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path '.venv/Scripts/python.exe')) {
    throw 'Create the virtual environment and install dependencies first; see README.md.'
}
& ./.venv/Scripts/python.exe -m uvicorn app.api:create_app --factory --host 127.0.0.1 --port 8000
