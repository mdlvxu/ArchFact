[CmdletBinding()]
param(
    [switch]$Quiet
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ServerRoot = Join-Path $ProjectRoot 'ArchFactServer'
$StatePath = Join-Path $ServerRoot '.runtime\launcher\processes.json'

function Test-TcpPort {
    param(
        [string]$HostName,
        [int]$Port
    )

    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $client.Connect($HostName, $Port)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Get-ServiceState {
    param(
        [object]$State,
        [string]$Name
    )

    if ($null -eq $State -or $null -eq $State.services) {
        return $null
    }
    $property = $State.services.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }
    return $property.Value
}

function Stop-TrackedProcess {
    param([int]$ProcessId)

    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $process) {
        return
    }

    # The launcher stores both the parent and listening process IDs.  Ending
    # only these confirmed processes is safer than recursively walking a
    # Windows process tree, which may contain unrelated child processes.
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Get-SystemBootMarker {
    return (Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).
        LastBootUpTime.ToUniversalTime().ToString('o')
}

function Test-ProcessDescendsFrom {
    param(
        [int]$ProcessId,
        [int]$AncestorProcessId
    )

    $currentProcessId = $ProcessId
    for ($depth = 0; $depth -lt 12; $depth++) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $currentProcessId" `
            -ErrorAction SilentlyContinue
        if ($null -eq $process) {
            return $false
        }

        $parentProcessId = [int]$process.ParentProcessId
        if ($parentProcessId -eq $AncestorProcessId) {
            return $true
        }
        if ($parentProcessId -le 0 -or $parentProcessId -eq $currentProcessId) {
            return $false
        }
        $currentProcessId = $parentProcessId
    }
    return $false
}

function Test-TrackedProcessIdentity {
    param(
        [int]$ProcessId,
        [object]$Service
    )

    # PIDs can be reused after a Windows restart.  Only stop a process when its
    # executable or command line still points back to this ArchFact checkout.
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" `
        -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return $false
    }

    $commandLine = [string]$process.CommandLine
    $executablePath = [string]$process.ExecutablePath
    $hasProjectIdentity = $commandLine.StartsWith($ProjectRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        $commandLine.IndexOf($ProjectRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -or
        $executablePath.StartsWith($ProjectRoot, [System.StringComparison]::OrdinalIgnoreCase)
    if ($hasProjectIdentity) {
        return $true
    }

    # Uvicorn reload workers can listen on 8080 with a multiprocessing command
    # line that does not include the project path.  The listener PID is safe to
    # stop only when it is a descendant of the launcher PID recorded in this
    # same Windows session.
    $listenerPid = if ($null -ne $Service.listenerPid) { [int]$Service.listenerPid } else { 0 }
    $launcherPid = if ($null -ne $Service.launcherPid) { [int]$Service.launcherPid } else { 0 }
    return $ProcessId -eq $listenerPid -and $launcherPid -gt 0 -and
        (Test-ProcessDescendsFrom -ProcessId $ProcessId -AncestorProcessId $launcherPid)
}

function Stop-TrackedService {
    param(
        [object]$Service,
        [string]$Name
    )

    if ($null -eq $Service -or -not $Service.startedByScript) {
        if (-not $Quiet) {
            Write-Host "$Name was not started by this launcher; leaving it running."
        }
        return
    }

    $candidateIds = @($Service.launcherPid, $Service.listenerPid) |
        Where-Object { $null -ne $_ } |
        Select-Object -Unique
    foreach ($candidateId in $candidateIds) {
        $currentProcess = Get-Process -Id ([int]$candidateId) -ErrorAction SilentlyContinue
        if ($null -eq $currentProcess) {
            # The launcher process may have already shut down its listener.
            continue
        }
        if (Test-TrackedProcessIdentity -ProcessId ([int]$candidateId) -Service $Service) {
            Stop-TrackedProcess -ProcessId ([int]$candidateId)
        }
        elseif (-not $Quiet) {
            Write-Warning "$Name PID $candidateId no longer belongs to this ArchFact checkout; it was not stopped."
        }
    }
    if (-not $Quiet) {
        Write-Host "$Name stopped."
    }
}

if (-not (Test-Path $StatePath)) {
    if (-not $Quiet) {
        Write-Host 'No ArchFact launcher state was found. Nothing was stopped.'
        Write-Host 'Use .\status-archfact.ps1 to inspect services started outside the launcher.'
    }
    exit 0
}

try {
    $state = Get-Content -Raw -Path $StatePath -Encoding UTF8 | ConvertFrom-Json
}
catch {
    Write-Error "Could not read $StatePath. No process was stopped."
    exit 1
}

try {
    $currentBootMarker = Get-SystemBootMarker
}
catch {
    Write-Error "Could not verify the current Windows session. No process was stopped."
    exit 1
}

if (-not $state.bootMarker -or $state.bootMarker -ne $currentBootMarker) {
    if (-not $Quiet) {
        Write-Warning 'The launcher state belongs to an earlier Windows session. No process was stopped.'
    }
    Remove-Item -LiteralPath $StatePath -Force -ErrorAction SilentlyContinue
    exit 0
}

$frontend = Get-ServiceState -State $state -Name 'frontend'
$backend = Get-ServiceState -State $state -Name 'backend'
$mongodb = Get-ServiceState -State $state -Name 'mongodb'

Stop-TrackedService -Service $frontend -Name 'Frontend'
Stop-TrackedService -Service $backend -Name 'Backend'

if ($null -ne $mongodb -and $mongodb.startedByScript) {
    if ($mongodb.mode -eq 'docker') {
        $dockerCommand = Get-Command 'docker.exe' -ErrorAction SilentlyContinue
        if ($dockerCommand) {
            & $dockerCommand.Source compose `
                -f (Join-Path $ServerRoot 'docker-compose.yml') `
                stop mongodb | Out-Null
        }
    }
    elseif (Test-TcpPort -HostName '127.0.0.1' -Port 27017) {
        $pythonExecutable = Join-Path $ServerRoot '.venv\Scripts\python.exe'
        if (Test-Path $pythonExecutable) {
            $shutdownCode = "from pymongo import MongoClient; MongoClient('mongodb://127.0.0.1:27017', serverSelectionTimeoutMS=2000).admin.command('shutdown', force=True)"
            $previousErrorActionPreference = $ErrorActionPreference
            $ErrorActionPreference = 'SilentlyContinue'
            & $pythonExecutable -c $shutdownCode 2>$null
            $ErrorActionPreference = $previousErrorActionPreference
        }

        $deadline = [DateTime]::UtcNow.AddSeconds(15)
        while (
            (Test-TcpPort -HostName '127.0.0.1' -Port 27017) -and
            [DateTime]::UtcNow -lt $deadline
        ) {
            Start-Sleep -Milliseconds 500
        }
    }

    if (Test-TcpPort -HostName '127.0.0.1' -Port 27017) {
        Stop-TrackedService -Service $mongodb -Name 'MongoDB'
    }
    elseif (-not $Quiet) {
        Write-Host 'MongoDB stopped cleanly.'
    }
}
elseif (-not $Quiet) {
    Write-Host 'MongoDB was not started by this launcher; leaving it running.'
}

Remove-Item -LiteralPath $StatePath -Force -ErrorAction SilentlyContinue

$remainingPorts = @(
    @(
        [pscustomobject]@{ Name = 'MongoDB'; Port = 27017 },
        [pscustomobject]@{ Name = 'Backend'; Port = 8080 },
        [pscustomobject]@{ Name = 'Frontend'; Port = 5173 }
    ) | Where-Object { Test-TcpPort -HostName '127.0.0.1' -Port $_.Port }
)

if (-not $Quiet) {
    if ($remainingPorts.Count -eq 0) {
        Write-Host 'ArchFact stopped.' -ForegroundColor Green
    }
    else {
        $names = ($remainingPorts | ForEach-Object { "$($_.Name):$($_.Port)" }) -join ', '
        Write-Warning "Some external services are still running: $names"
    }
}
