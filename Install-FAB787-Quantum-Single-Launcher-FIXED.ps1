[CmdletBinding()]
param(
    [string]$ProjectRoot = (Join-Path $HOME 'Documents\GitHub\futures-mission-control')
)

$ErrorActionPreference = 'Stop'

function Write-Step([string]$Message) {
    Write-Host "`n=== $Message ===" -ForegroundColor Cyan
}

try {
    $ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
    $MainFile = Join-Path $ProjectRoot 'mission_control\main.py'
    $VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

    if (-not (Test-Path -LiteralPath $MainFile -PathType Leaf)) {
        throw "Project main file was not found: $MainFile"
    }
    if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
        throw "Project virtual-environment Python was not found: $VenvPython. No desktop shortcuts were moved."
    }

    # Always force an array: PowerShell unwraps a one-item pipeline result to a
    # scalar string. Indexing that scalar with [0] returns only its first character
    # (for example, 'C'), which caused the previous installer to build a bad path.
    $DesktopCandidates = @(
        @(
            [Environment]::GetFolderPath('DesktopDirectory'),
            (Join-Path $HOME 'OneDrive\Desktop'),
            (Join-Path $HOME 'Desktop')
        ) |
            Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Container) } |
            ForEach-Object { [System.IO.Path]::GetFullPath([string]$_) } |
            Select-Object -Unique
    )

    if ($DesktopCandidates.Count -eq 0) {
        throw 'Could not locate the Windows Desktop folder. No shortcuts were changed.'
    }
    $PrimaryDesktop = [string]($DesktopCandidates | Select-Object -First 1)
    if (-not [System.IO.Path]::IsPathRooted($PrimaryDesktop) -or
        -not (Test-Path -LiteralPath $PrimaryDesktop -PathType Container)) {
        throw "Resolved Desktop path is invalid: $PrimaryDesktop. No shortcuts were changed."
    }

    $LauncherPath = Join-Path $ProjectRoot 'FAB787-QUANTUM-Launcher.ps1'
    $LauncherContent = @'
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
'@

    Write-Step 'Install one managed launcher in the project folder'
    Set-Content -LiteralPath $LauncherPath -Value $LauncherContent -Encoding UTF8
    Write-Host "Launcher script: $LauncherPath" -ForegroundColor Green

    Write-Step 'Move only old FAB787 / QUANTUM desktop shortcuts into a backup folder'
    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $moved = 0
    foreach ($desktop in $DesktopCandidates) {
        $oldLinks = @(Get-ChildItem -LiteralPath $desktop -Filter '*.lnk' -File -ErrorAction SilentlyContinue |
            Where-Object { $_.BaseName -match '(?i)FAB787|QUANTUM' })
        if ($oldLinks.Count -eq 0) { continue }
        $backupFolder = Join-Path $desktop "FAB787-Old-Shortcuts-$timestamp"
        New-Item -ItemType Directory -Force -Path $backupFolder | Out-Null
        foreach ($link in $oldLinks) {
            Move-Item -LiteralPath $link.FullName -Destination (Join-Path $backupFolder $link.Name) -Force
            $moved++
            Write-Host "Backed up: $($link.Name)" -ForegroundColor DarkYellow
        }
        Write-Host "Backup folder: $backupFolder" -ForegroundColor Yellow
    }
    if ($moved -eq 0) { Write-Host 'No old FAB787 / QUANTUM shortcut links found.' -ForegroundColor DarkGray }

    Write-Step 'Create the single FAB787 QUANTUM desktop shortcut'
    $shortcutPath = [System.IO.Path]::GetFullPath(
        (Join-Path -Path $PrimaryDesktop -ChildPath 'FAB787 QUANTUM.lnk')
    )
    if (-not [System.IO.Path]::IsPathRooted($shortcutPath) -or
        -not (Test-Path -LiteralPath (Split-Path -Parent $shortcutPath) -PathType Container)) {
        throw "Shortcut destination is invalid: $shortcutPath"
    }
    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $powershell
    $shortcut.Arguments = "-NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$LauncherPath`""
    $shortcut.WorkingDirectory = $ProjectRoot
    $shortcut.Description = 'Start or open FAB787 QUANTUM using the project virtual environment.'
    $shortcut.WindowStyle = 7

    $iconCandidates = @()
    foreach ($desktop in $DesktopCandidates) {
        $iconCandidates += Get-ChildItem -LiteralPath $desktop -Filter '*.ico' -File -ErrorAction SilentlyContinue |
            Where-Object { $_.BaseName -match '(?i)FAB787|QUANTUM' }
    }
    $iconCandidates += Get-ChildItem -LiteralPath $ProjectRoot -Filter '*.ico' -File -ErrorAction SilentlyContinue |
        Where-Object { $_.BaseName -match '(?i)FAB787|QUANTUM' }
    $icon = $iconCandidates | Select-Object -First 1
    if ($icon) {
        $shortcut.IconLocation = "$($icon.FullName),0"
        Write-Host "Using existing brand icon: $($icon.FullName)" -ForegroundColor Green
    } else {
        $shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,167"
        Write-Host 'No existing FAB787 icon found; using a standard Windows application icon.' -ForegroundColor Yellow
    }
    $shortcut.Save()
    if (-not (Test-Path -LiteralPath $shortcutPath -PathType Leaf)) {
        throw "Windows did not create the expected shortcut file: $shortcutPath"
    }
    Write-Host "Created: $shortcutPath" -ForegroundColor Green
    Write-Host "Only old FAB787 / QUANTUM .lnk files were moved. Other desktop files and shortcuts were left alone." -ForegroundColor Green

    Write-Step 'Start / update Mission Control now'
    $launcherRun = Start-Process -FilePath $powershell `
        -ArgumentList "-NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$LauncherPath`"" `
        -WorkingDirectory $ProjectRoot -WindowStyle Hidden -Wait -PassThru
    if ($launcherRun.ExitCode -ne 0) {
        throw "Launcher reported exit code $($launcherRun.ExitCode). See $env:LOCALAPPDATA\FAB787-QUANTUM\logs\launcher.log"
    }

    Write-Host "`nInstallation complete. Keep only this desktop shortcut: FAB787 QUANTUM" -ForegroundColor Green
    Write-Host 'To stop the app later, run this from the project folder:' -ForegroundColor Cyan
    Write-Host '.\FAB787-QUANTUM-Launcher.ps1 -Stop'
} catch {
    Write-Host "`nINSTALLATION ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'No unrelated desktop shortcuts or project files were intentionally changed.' -ForegroundColor Yellow
    exit 1
}
