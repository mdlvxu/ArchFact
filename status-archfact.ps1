[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$StatePath = Join-Path $ProjectRoot 'ArchFactServer\.runtime\launcher\processes.json'

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

$managed = Test-Path $StatePath
$rows = @(
    [pscustomobject]@{
        Service = 'MongoDB'
        Ready = Test-TcpPort -HostName '127.0.0.1' -Port 27017
        Address = '127.0.0.1:27017'
    },
    [pscustomobject]@{
        Service = 'Backend'
        Ready = Test-HttpEndpoint -Url 'http://127.0.0.1:8080/api/v1/health'
        Address = 'http://127.0.0.1:8080/docs'
    },
    [pscustomobject]@{
        Service = 'Frontend'
        Ready = (
            (Test-HttpEndpoint -Url 'http://127.0.0.1:5173/') -and
            (Test-HttpEndpoint -Url 'http://127.0.0.1:5173/api/v1/health')
        )
        Address = 'http://127.0.0.1:5173/'
    }
)

$rows | Format-Table -AutoSize
Write-Host "Launcher state: $(if ($managed) { 'present' } else { 'not present' })"

$notReady = @($rows | Where-Object { -not $_.Ready })
if ($notReady.Count -gt 0) {
    exit 1
}
exit 0
