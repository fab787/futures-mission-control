[CmdletBinding()]
param([switch]$Stop)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$HostAddress = '127.0.0.1'
$Port = 8000
$BaseUrl = "http://${HostAddress}:$Port"
$ExpectedRoutes = @(
    '/api/news/webull',
    '/api/news/intelligence',
    '/api/calendar/events',
    '/api/gex/snapshot'
)
$LogRoot = Join-Path $env:LOCALAPPDATA 'FAB787-QUANTUM\logs'
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
$LogFile = Join-Path $LogRoot 'launcher.log'

function Write-Log([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
}

function Get-PortListeners {
    @(Get-NetTCPConnection -LocalAddress $HostAddress -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
}

function Get-ApiState {
    try {
        $health = Invoke-RestMethod -Uri "$BaseUrl/api/health" -TimeoutSec 2
        $openapi = Invoke-RestMethod -Uri "$BaseUrl/openapi.json" -TimeoutSec 3
        $paths = @($openapi.paths.PSObject.Properties.Name)
        $missing = @($ExpectedRoutes | Where-Object { $_ -notin $paths })
        return [pscustomobject]@{
            Healthy = ($health.status -eq 'ok')
            Paths = $paths
            Missing = $missing
        }
    } catch {
        return $null
    }
}

function Test-OwnedMissionControlProcess([int]$ProcessId) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if (-not $proc) { return $false }
    if ($proc.Name -notmatch '^(python|pythonw)(\.exe)?$') { return $false }
    $command = [string]$proc.CommandLine
    if ($command -notmatch '(?i)-m\s+uvicorn\s+mission_control\.main:app') { return $false }
    if ($command -notmatch '(?i)--port\s+8000') { return $false }
    if ($command -notmatch '(?i)--host\s+127\.0\.0\.1') { return $false }
    try {
        $owner = Invoke-CimMethod -InputObject $proc -MethodName GetOwner -ErrorAction Stop
        if ($owner.ReturnValue -ne 0 -or $owner.User -ne $env:USERNAME) { return $false }
    } catch {
        return $false
    }
    return $true
}

function Stop-VerifiedMissionControl {
    $listenerIds = @(Get-PortListeners)
    if ($listenerIds.Count -eq 0) { return }
    foreach ($listenerId in $listenerIds) {
        $id = [int]$listenerId
        if (-not (Test-OwnedMissionControlProcess -ProcessId $id)) {
            throw "Port 8000 is owned by PID $id, but it does not match this user's verified Mission Control Uvicorn command. Refusing to stop it."
        }
        Write-Log "Stopping prior Mission Control server PID $id on 127.0.0.1:8000."
        Stop-Process -Id $id -Force
    }
    $deadline = (Get-Date).AddSeconds(12)
    do {
        Start-Sleep -Milliseconds 300
        if (@(Get-PortListeners).Count -eq 0) { return }
    } while ((Get-Date) -lt $deadline)
    throw 'The prior Mission Control server did not release port 8000.'
}

try {
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "Virtual environment Python is missing: $Python"
    }
    Set-Location -LiteralPath $ProjectRoot

    if ($Stop) {
        Stop-VerifiedMissionControl
        Write-Log 'Mission Control stopped by launcher -Stop option.'
        Write-Host 'FAB787 QUANTUM stopped.' -ForegroundColor Yellow
        exit 0
    }

    $state = Get-ApiState
    if ($state -and $state.Healthy -and $state.Missing.Count -eq 0) {
        Write-Log 'Updated Mission Control already running; opening welcome page.'
        Start-Process "$BaseUrl/welcome"
        exit 0
    }

    $listeners = @(Get-PortListeners)
    if ($listeners.Count -gt 0) {
        Write-Log 'Existing port-8000 server does not expose all patched routes; verifying process before restart.'
        Stop-VerifiedMissionControl
    }

    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $stdout = Join-Path $LogRoot "uvicorn_$stamp.stdout.log"
    $stderr = Join-Path $LogRoot "uvicorn_$stamp.stderr.log"
    $arguments = '-m uvicorn mission_control.main:app --host 127.0.0.1 --port 8000'
    $server = Start-Process -FilePath $Python -ArgumentList $arguments -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    Write-Log "Started Mission Control PID $($server.Id) with $Python."

    $deadline = (Get-Date).AddSeconds(35)
    $ready = $false
    $lastState = $null
    do {
        Start-Sleep -Milliseconds 500
        $lastState = Get-ApiState
        if ($lastState -and $lastState.Healthy -and $lastState.Missing.Count -eq 0) {
            $ready = $true
            break
        }
        $server.Refresh()
        if ($server.HasExited) { break }
    } while ((Get-Date) -lt $deadline)

    if (-not $ready) {
        $detail = if ($lastState -and $lastState.Missing.Count) {
            'Missing routes: ' + ($lastState.Missing -join ', ')
        } else { 'The local API did not become healthy.' }
        $tail = if (Test-Path $stderr) { (Get-Content $stderr -Tail 30 -ErrorAction SilentlyContinue) -join "`n" } else { '' }
        Write-Log "Startup verification failed. $detail $tail"
        throw "Mission Control did not pass startup verification. $detail`nCheck log: $stderr`n$tail"
    }

    Write-Log 'All four intelligence routes verified from the live OpenAPI document.'
    Start-Process "$BaseUrl/welcome"
    Write-Host 'FAB787 QUANTUM is running from the project .venv.' -ForegroundColor Green
    Write-Host 'Verified routes:' -ForegroundColor Cyan
    $ExpectedRoutes | ForEach-Object { Write-Host "  $_" }
} catch {
    Write-Log "ERROR: $($_.Exception.Message)"
    Write-Host $_.Exception.Message -ForegroundColor Red
    try {
        Add-Type -AssemblyName PresentationFramework -ErrorAction SilentlyContinue
        [System.Windows.MessageBox]::Show($_.Exception.Message, 'FAB787 QUANTUM Launcher', 'OK', 'Error') | Out-Null
    } catch { }
    exit 1
}
