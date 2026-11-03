param([ValidateSet('api','web','test')][string]$Target = 'api')
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$python = Join-Path (Get-Location) '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw '请先执行 python -m venv .venv 并安装依赖，见 README.md' }
switch ($Target) {
    'api' { $env:PYTHONPATH = 'backend'; & $python -m uvicorn workbench.main:app --reload --host 127.0.0.1 --port 8000 }
    'web' { Set-Location frontend; & npm.cmd run dev -- --host 127.0.0.1 }
    'test' { & $python -m pytest -q }
}
