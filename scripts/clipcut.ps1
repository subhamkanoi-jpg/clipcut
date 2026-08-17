# ClipCut one-click launcher (target of the desktop shortcut).
# Starts MongoDB (if stopped), the API, the worker, and the frontend, waits for
# the frontend to finish compiling, then opens it in the browser.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

function Fail($msg) {
    Write-Host ""
    Write-Host "ClipCut could not start:" -ForegroundColor Red
    Write-Host "  $msg"
    Write-Host ""
    Write-Host "Press any key to close..."
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    exit 1
}

try {
    # MongoDB is set to auto-start, but start it if it happens to be stopped.
    $svc = Get-Service -Name MongoDB -ErrorAction SilentlyContinue
    if ($null -eq $svc) {
        Fail "MongoDB service not found. Install MongoDB Community, or edit backend\.env to point MONGO_URL at another server."
    }
    if ($svc.Status -ne "Running") {
        Write-Host "Starting MongoDB..."
        try { Start-Service -Name MongoDB -ErrorAction Stop }
        catch { Fail "MongoDB is stopped and could not be started automatically (needs admin). Start the 'MongoDB' service, then retry." }
    }

    # Start API + worker + frontend (dev.ps1 skips anything already running).
    Write-Host "Launching ClipCut services..."
    & (Join-Path $PSScriptRoot "dev.ps1")

    # Wait for the frontend port to accept connections before opening the browser.
    # A raw TCP check on 127.0.0.1 avoids PowerShell's localhost/IPv6 resolution
    # quirks (Invoke-WebRequest can hang on ::1). A cold CRA start takes ~30s to
    # compile; the port binds first, so add a short grace pause once it is up.
    Write-Host ""
    Write-Host "Waiting for the ClipCut UI to be ready (first start can take ~30s)..."
    $ready = $false
    foreach ($i in 1..90) {
        try {
            $c = New-Object System.Net.Sockets.TcpClient
            $c.Connect("127.0.0.1", 3000)
            if ($c.Connected) { $c.Close(); $ready = $true; break }
        } catch { }
        Start-Sleep -Seconds 1
    }

    if ($ready) {
        Start-Sleep -Seconds 2  # let a cold webpack build settle before the browser hits it
        Write-Host "ClipCut is ready. Opening the browser..." -ForegroundColor Green
    } else {
        Write-Host "The UI did not respond in time. Opening anyway - check the frontend window if it does not load." -ForegroundColor Yellow
    }
    Start-Process "http://localhost:3000"
}
catch {
    Fail $_.Exception.Message
}
