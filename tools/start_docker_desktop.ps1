# Local Windows recovery launcher for Docker Desktop's orphaned AF_UNIX socket.
# It preserves broken temporary directories and never resets Docker data.
param([int]$TimeoutSeconds = 120)
$ErrorActionPreference = 'Stop'
Start-Transcript -Path 'D:\Docker\desktop-startup.log' -Append | Out-Null
$dockerExe = 'D:\Docker\DockerDesktop\resources\bin\docker.exe'
$desktopExe = 'D:\Docker\DockerDesktop\Docker Desktop.exe'
$socketDirectory = Join-Path $env:LOCALAPPDATA 'docker-secrets-engine'
$backendLog = Join-Path $env:LOCALAPPDATA 'Docker\log\host\com.docker.backend.exe.log'

function Test-DockerReady {
    $probe = New-Object System.Diagnostics.Process
    $probe.StartInfo.FileName = $dockerExe
    $probe.StartInfo.Arguments = 'info --format "{{.ServerVersion}}"'
    $probe.StartInfo.UseShellExecute = $false
    $probe.StartInfo.CreateNoWindow = $true
    $probe.StartInfo.RedirectStandardOutput = $true
    $probe.StartInfo.RedirectStandardError = $true
    try {
        [void]$probe.Start()
        if (!$probe.WaitForExit(5000)) { $probe.Kill(); return $false }
        return $probe.ExitCode -eq 0
    } finally { $probe.Dispose() }
}

function Repair-StaleSocket {
    if (!(Test-Path -LiteralPath $socketDirectory)) { return $false }
    if (!(Test-Path -LiteralPath $backendLog)) { return $false }
    $failure = Select-String -LiteralPath $backendLog -Pattern 'initializing Secrets Engine.*engine\.sock.*The file cannot be accessed' |
        Select-Object -Last 1
    if (!$failure) { return $false }
    $entries = @(Get-ChildItem -LiteralPath $socketDirectory -Force)
    if (!$entries.Count) { return $false }
    foreach ($entry in $entries) {
        if ($entry.Name -notin @('engine.sock', 'engine.sock.stale') -or
            $entry.Length -ne 0 -or $entry.PSIsContainer) {
            throw 'Unexpected contents in socket directory; refusing automatic recovery.'
        }
    }
    # The engine is unavailable; stop only the failed Desktop processes.
    $failedProcesses = Get-Process -Name 'Docker Desktop','com.docker.backend' -ErrorAction SilentlyContinue
    if ($failure.Line -notmatch '^\[([^\]]+)\]') { return $false }
    $failureTime = [DateTimeOffset]::Parse($Matches[1]).UtcDateTime
    if (@($failedProcesses | Where-Object { $_.StartTime.ToUniversalTime() -gt $failureTime }).Count) {
        return $false # A newer Desktop instance is still starting.
    }
    $failedProcesses | Stop-Process -Force -ErrorAction SilentlyContinue
    $failedProcesses | Wait-Process -Timeout 15 -ErrorAction SilentlyContinue
    $unixPath = (& wsl.exe -d Ubuntu -- wslpath -u $socketDirectory | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or !$unixPath.StartsWith('/mnt/')) {
        throw 'WSL could not resolve the temporary directory.'
    }
    $backupPath = $unixPath + '.stale-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
    & wsl.exe -d Ubuntu -- python3 -c 'import os,sys; os.rename(sys.argv[1],sys.argv[2])' $unixPath $backupPath
    if ($LASTEXITCODE -ne 0) { throw 'WSL could not preserve the stale socket directory.' }
    Write-Host "Preserved stale sockets: $backupPath"
    return $true
}

if (Test-DockerReady) { Write-Output 'Docker is already ready.'; exit 0 }
$repaired = Repair-StaleSocket
Start-Process -FilePath $desktopExe -WindowStyle Hidden
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
    if (Test-DockerReady) { Write-Output 'Docker is ready.'; exit 0 }
    if (!$repaired -and (Repair-StaleSocket)) {
        $repaired = $true
        Start-Process -FilePath $desktopExe -WindowStyle Hidden
    }
}
throw 'Docker did not become ready. Inspect its latest backend log; no data was reset.'
