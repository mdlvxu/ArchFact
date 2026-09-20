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

function Stop-ProcessTree {
    param([int]$ProcessId)

    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $process) {
        return
    }

    try {
        $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId" `
            -ErrorAction Stop
        foreach ($child in $children) {
            Stop-ProcessTree -ProcessId ([int]$child.ProcessId)
        }
    }
    catch {
        # The listener PID stored in the state file is stopped separately below.
    }

    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
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
        Stop-ProcessTree -ProcessId ([int]$candidateId)
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
