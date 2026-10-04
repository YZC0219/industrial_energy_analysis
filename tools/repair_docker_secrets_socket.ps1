# Run only after explicit authorization to rename this existing runtime directory.
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path 'D:\industrial_energy_analysis\tmp\docker-start' | Out-Null
Start-Transcript -Path 'D:\industrial_energy_analysis\tmp\docker-start\repair.log' -Append | Out-Null
$runtimeFolder = [System.IO.Path]::GetFullPath('C:\Users\32074\AppData\Local\docker-secrets-engine')
$allowedFolder = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($env:LOCALAPPDATA, 'docker-secrets-engine'))
if ($runtimeFolder -ne $allowedFolder) { throw 'Unexpected runtime directory; no changes made.' }
if (Get-Process 'Docker Desktop','com.docker.backend' -ErrorAction SilentlyContinue) {
    throw 'Docker must be fully stopped before this repair; no changes made.'
}
$runtimeItem = Get-Item -LiteralPath $runtimeFolder -Force
if ($runtimeItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
    throw 'Runtime parent is a reparse point; no changes made.'
}
$entries = @(Get-ChildItem -LiteralPath $runtimeFolder -Force)
if ($entries.Count -ne 1 -or $entries[0].Name -ne 'engine.sock' -or $entries[0].Length -ne 0) {
    throw 'Directory contents differ from the reviewed zero-byte socket; no changes made.'
}
$backupName = 'docker-secrets-engine.backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
$backupPath = Join-Path (Split-Path -Parent $runtimeFolder) $backupName
if (Test-Path -LiteralPath $backupPath) { throw 'Backup already exists; no changes made.' }
[System.IO.Directory]::Move($runtimeFolder, $backupPath)
Write-Output ('Preserved socket directory: ' + $backupPath)
$env:TEMP = 'D:\industrial_energy_analysis\tmp\docker-start'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
Start-Process -FilePath 'D:\Docker\DockerDesktop\Docker Desktop.exe' -WindowStyle Hidden
