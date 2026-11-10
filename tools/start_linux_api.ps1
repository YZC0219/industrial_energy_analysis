param([int]$Port = 8010)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$tunnel = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 13307 -State Listen -ErrorAction SilentlyContinue
if (-not $tunnel) {
    Start-Process -FilePath 'ssh.exe' -ArgumentList @('-N','-L','127.0.0.1:13307:127.0.0.1:3307','-i','C:/Users/32074/.ssh/id_ed25519_github_yzc0219','-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','yzc@192.168.21.131') -WindowStyle Hidden
    Start-Sleep -Seconds 2
}
python -m uvicorn src.api:app --host 127.0.0.1 --port $Port --env-file tmp/linux-mysql.env
