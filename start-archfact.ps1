[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [ValidateRange(15, 600)]
    [int]$TimeoutSeconds = 120
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ClientRoot = Join-Path $ProjectRoot 'ArchFactClient'
$ServerRoot = Join-Path $ProjectRoot 'ArchFactServer'
$RuntimeRoot = Join-Path $ServerRoot '.runtime\launcher'
$StatePath = Join-Path $RuntimeRoot 'processes.json'
$ClientLogRoot = Join-Path $ClientRoot '.runtime-logs'
$ServerLogRoot = Join-Path $ServerRoot '.runtime-logs'
$RunStamp = Get-Date -Format 'yyyyMMdd-HHmmss'

$BackendHealthUrl = 'http://127.0.0.1:8080/api/v1/health'
$FrontendUrl = 'http://127.0.0.1:5173/'
$FrontendHealthUrl = 'http://127.0.0.1:5173/api/v1/health'

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

function Test-HttpEndpoint {
    param([string]$Url)

    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 400
    }
    catch {
        return $false
    }
}

function Wait-Until {
    param(
        [scriptblock]$Condition,
        [string]$Description,
        [int]$Seconds = $TimeoutSeconds
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        if (& $Condition) {
            return
        }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)

    throw "Timed out waiting for $Description after $Seconds seconds."
}

function Get-ListeningProcessId {
    param([int]$Port)

    try {
        $connection = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop |
            Select-Object -First 1
        if ($null -ne $connection) {
            return [int]$connection.OwningProcess
        }
    }
    catch {
        return $null
    }
    return $null
}

function Get-PriorService {
    param(
        [object]$PriorState,
        [string]$Name
    )

    if ($null -eq $PriorState -or $null -eq $PriorState.services) {
        return $null
    }
    $property = $PriorState.services.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }
    return $property.Value
}

function Test-TrackedService {
    param([object]$Service)

    if ($null -eq $Service -or -not $Service.startedByScript) {
        return $false
    }
    $candidateIds = @($Service.listenerPid, $Service.launcherPid) |
        Where-Object { $null -ne $_ } |
        Select-Object -Unique
    foreach ($candidateId in $candidateIds) {
        if (Get-Process -Id $candidateId -ErrorAction SilentlyContinue) {
            return $true
        }
    }
    return $false
}

function New-ServiceState {
    param(
        [string]$Mode,
        [bool]$StartedByScript,
        [AllowNull()][object]$LauncherPid,
        [AllowNull()][object]$ListenerPid,
        [string]$LogPath
    )

    return [ordered]@{
        mode = $Mode
        startedByScript = $StartedByScript
        launcherPid = $LauncherPid
        listenerPid = $ListenerPid
        logPath = $LogPath
    }
}

function Save-State {
    $state = [ordered]@{
        schemaVersion = 1
        projectRoot = $ProjectRoot
        startedAt = [DateTime]::UtcNow.ToString('o')
        services = $services
    }
    $state | ConvertTo-Json -Depth 6 | Set-Content -Path $StatePath -Encoding UTF8
}

function Show-LogTail {
    param([string[]]$Paths)

    foreach ($path in $Paths) {
        if ($path -and (Test-Path $path)) {
            Write-Host ""
            Write-Host "Last lines from $path" -ForegroundColor Yellow
            Get-Content -Path $path -Tail 20 -ErrorAction SilentlyContinue
        }
    }
}

if (-not (Test-Path $ClientRoot) -or -not (Test-Path $ServerRoot)) {
    throw 'Run this script from an ArchFact checkout containing ArchFactClient and ArchFactServer.'
}

New-Item -ItemType Directory -Force -Path $RuntimeRoot, $ClientLogRoot, $ServerLogRoot |
    Out-Null

$priorState = $null
if (Test-Path $StatePath) {
    try {
        $priorState = Get-Content -Raw -Path $StatePath -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        Write-Warning 'The previous launcher state was unreadable and will be replaced.'
    }
}

$services = [ordered]@{}

try {
    Write-Host '[1/3] MongoDB' -ForegroundColor Cyan
    if (Test-TcpPort -HostName '127.0.0.1' -Port 27017) {
        $priorMongo = Get-PriorService -PriorState $priorState -Name 'mongodb'
        if (Test-TrackedService -Service $priorMongo) {
            $services.mongodb = $priorMongo
            Write-Host 'MongoDB is already running (managed by this launcher).'
        }
        else {
            $services.mongodb = New-ServiceState -Mode 'external' -StartedByScript $false `
                -LauncherPid $null -ListenerPid (Get-ListeningProcessId -Port 27017) -LogPath ''
            Write-Host 'MongoDB is already running (external process).'
        }
    }
    else {
        $mongoExecutable = $null
        if ($env:ARCHFACT_MONGOD -and (Test-Path $env:ARCHFACT_MONGOD)) {
            $mongoExecutable = (Resolve-Path $env:ARCHFACT_MONGOD).Path
        }
        if (-not $mongoExecutable) {
            $mongoExecutable = Get-ChildItem `
                -Path (Join-Path $ServerRoot '.runtime') `
                -Directory `
                -Filter 'mongodb-*' `
                -ErrorAction SilentlyContinue |
                ForEach-Object {
                    Get-ChildItem `
                        -Path $_.FullName `
                        -Filter 'mongod.exe' `
                        -File `
                        -Recurse `
                        -ErrorAction SilentlyContinue
                } |
                Select-Object -First 1 -ExpandProperty FullName
        }
        if (-not $mongoExecutable) {
            $mongoCommand = Get-Command 'mongod.exe' -ErrorAction SilentlyContinue
            if ($mongoCommand) {
                $mongoExecutable = $mongoCommand.Source
            }
        }

        if ($mongoExecutable) {
            $mongoDataRoot = Join-Path $ServerRoot '.runtime\data'
            $mongoLogRoot = Join-Path $ServerRoot '.runtime\logs'
            $mongoLogPath = Join-Path $mongoLogRoot 'mongodb.log'
            New-Item -ItemType Directory -Force -Path $mongoDataRoot, $mongoLogRoot | Out-Null
            $mongoProcess = Start-Process `
                -FilePath $mongoExecutable `
                -ArgumentList @(
                    '--dbpath', "`"$mongoDataRoot`"",
                    '--bind_ip', '127.0.0.1',
                    '--port', '27017',
                    '--logpath', "`"$mongoLogPath`"",
                    '--logappend'
                ) `
                -WorkingDirectory $ServerRoot `
                -WindowStyle Hidden `
                -PassThru
            Wait-Until `
                -Condition { Test-TcpPort -HostName '127.0.0.1' -Port 27017 } `
                -Description 'MongoDB'
            $services.mongodb = New-ServiceState -Mode 'process' -StartedByScript $true `
                -LauncherPid $mongoProcess.Id -ListenerPid (Get-ListeningProcessId -Port 27017) `
                -LogPath $mongoLogPath
            Write-Host 'MongoDB is ready.'
        }
        else {
            $dockerCommand = Get-Command 'docker.exe' -ErrorAction SilentlyContinue
            if (-not $dockerCommand) {
                throw 'MongoDB was not found. Install MongoDB, place the portable server under ArchFactServer\.runtime, or start Docker Desktop.'
            }
            & $dockerCommand.Source compose -f (Join-Path $ServerRoot 'docker-compose.yml') up -d mongodb
            if ($LASTEXITCODE -ne 0) {
                throw 'Docker could not start MongoDB.'
            }
            Wait-Until `
                -Condition { Test-TcpPort -HostName '127.0.0.1' -Port 27017 } `
                -Description 'MongoDB Docker container'
            $services.mongodb = New-ServiceState -Mode 'docker' -StartedByScript $true `
                -LauncherPid $null -ListenerPid (Get-ListeningProcessId -Port 27017) -LogPath ''
            Write-Host 'MongoDB Docker container is ready.'
        }
    }
    Save-State

    Write-Host '[2/3] ArchFactServer' -ForegroundColor Cyan
    if (Test-HttpEndpoint -Url $BackendHealthUrl) {
        $priorBackend = Get-PriorService -PriorState $priorState -Name 'backend'
        if (Test-TrackedService -Service $priorBackend) {
            $services.backend = $priorBackend
            Write-Host 'Backend is already running (managed by this launcher).'
        }
        else {
            $services.backend = New-ServiceState -Mode 'external' -StartedByScript $false `
                -LauncherPid $null -ListenerPid (Get-ListeningProcessId -Port 8080) -LogPath ''
            Write-Host 'Backend is already running (external process).'
        }
    }
    else {
        if (Test-TcpPort -HostName '127.0.0.1' -Port 8080) {
            throw 'Port 8080 is occupied, but the ArchFact backend health check failed.'
        }
        $pythonExecutable = Join-Path $ServerRoot '.venv\Scripts\python.exe'
        if (-not (Test-Path $pythonExecutable)) {
            throw 'Backend virtual environment is missing. Follow SETUP_WINDOWS.md to create ArchFactServer\.venv.'
        }
        $backendOutputLog = Join-Path $ServerLogRoot "backend-$RunStamp.out.log"
        $backendErrorLog = Join-Path $ServerLogRoot "backend-$RunStamp.err.log"
        $backendProcess = Start-Process `
            -FilePath $pythonExecutable `
            -ArgumentList @('run.py') `
            -WorkingDirectory $ServerRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $backendOutputLog `
            -RedirectStandardError $backendErrorLog `
            -PassThru
        Wait-Until `
            -Condition { Test-HttpEndpoint -Url $BackendHealthUrl } `
            -Description 'ArchFact backend'
        $services.backend = New-ServiceState -Mode 'process' -StartedByScript $true `
            -LauncherPid $backendProcess.Id -ListenerPid (Get-ListeningProcessId -Port 8080) `
            -LogPath $backendErrorLog
        Write-Host 'Backend is ready.'
    }
    Save-State

    Write-Host '[3/3] ArchFactClient' -ForegroundColor Cyan
    if (Test-HttpEndpoint -Url $FrontendUrl) {
        if (-not (Test-HttpEndpoint -Url $FrontendHealthUrl)) {
            throw 'The frontend is running, but its backend proxy health check failed.'
        }
        $priorFrontend = Get-PriorService -PriorState $priorState -Name 'frontend'
        if (Test-TrackedService -Service $priorFrontend) {
            $services.frontend = $priorFrontend
            Write-Host 'Frontend is already running (managed by this launcher).'
        }
        else {
            $services.frontend = New-ServiceState -Mode 'external' -StartedByScript $false `
                -LauncherPid $null -ListenerPid (Get-ListeningProcessId -Port 5173) -LogPath ''
            Write-Host 'Frontend is already running (external process).'
        }
    }
    else {
        if (Test-TcpPort -HostName '127.0.0.1' -Port 5173) {
            throw 'Port 5173 is occupied, but the ArchFact frontend health check failed.'
        }
        if (-not (Test-Path (Join-Path $ClientRoot 'node_modules'))) {
            throw 'Frontend dependencies are missing. Run pnpm install in ArchFactClient first.'
        }
        $viteCommand = Join-Path $ClientRoot 'node_modules\.bin\vite.cmd'
        if (-not (Test-Path $viteCommand)) {
            throw 'The local Vite launcher is missing. Run pnpm install in ArchFactClient first.'
        }
        $frontendOutputLog = Join-Path $ClientLogRoot "frontend-$RunStamp.out.log"
        $frontendErrorLog = Join-Path $ClientLogRoot "frontend-$RunStamp.err.log"
        $frontendProcess = Start-Process `
            -FilePath $viteCommand `
            -ArgumentList @('--host', '127.0.0.1') `
            -WorkingDirectory $ClientRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $frontendOutputLog `
            -RedirectStandardError $frontendErrorLog `
            -PassThru
        Wait-Until `
            -Condition {
                (Test-HttpEndpoint -Url $FrontendUrl) -and
                (Test-HttpEndpoint -Url $FrontendHealthUrl)
            } `
            -Description 'ArchFact frontend'
        $services.frontend = New-ServiceState -Mode 'process' -StartedByScript $true `
            -LauncherPid $frontendProcess.Id -ListenerPid (Get-ListeningProcessId -Port 5173) `
            -LogPath $frontendErrorLog
        Write-Host 'Frontend is ready.'
    }
    Save-State

    Write-Host ''
    Write-Host 'ArchFact is ready.' -ForegroundColor Green
    Write-Host "Frontend: $FrontendUrl"
    Write-Host 'API docs: http://127.0.0.1:8080/docs'
    Write-Host 'Status:   status-archfact.cmd'
    Write-Host 'Stop:     stop-archfact.cmd'

    if (-not $NoBrowser) {
        Start-Process $FrontendUrl
    }
}
catch {
    Write-Host ''
    Write-Host "ArchFact startup failed: $($_.Exception.Message)" -ForegroundColor Red
    $logPaths = @()
    foreach ($serviceName in @('frontend', 'backend', 'mongodb')) {
        if ($services.Contains($serviceName) -and $services[$serviceName].logPath) {
            $logPaths += [string]$services[$serviceName].logPath
        }
    }
    Show-LogTail -Paths $logPaths
    if (Test-Path $StatePath) {
        Write-Host ''
        Write-Host 'Cleaning up services started during this attempt...'
        & (Join-Path $ProjectRoot 'stop-archfact.ps1') -Quiet
    }
    exit 1
}
