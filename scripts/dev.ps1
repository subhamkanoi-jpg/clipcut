# Start ClipCut: API, job worker, and frontend.
#
# ClipCut runs as three processes. The API only enqueues jobs; the worker runs
# all transcription and ffmpeg work. Starting the API alone will accept uploads
# that then never progress past "transcribing".
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\dev.ps1

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv-local\Scripts\python.exe"
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"

# --- preflight ---------------------------------------------------------------

if (-not (Test-Path $py)) {
    throw "venv missing at $py. Create it, then install: fastapi uvicorn pymongo python-dotenv python-multipart requests opencv-python-headless==4.11.0.86 cloudinary pillow httpx"
}
if (-not (Test-Path (Join-Path $backend ".env"))) {
    throw "backend\.env missing. It needs MONGO_URL, DB_NAME, and ELEVENLABS_API_KEY."
}
if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
    throw "frontend\node_modules missing. Run 'npm install' in frontend\ first."
}

$mongo = Get-Service -Name MongoDB -ErrorAction SilentlyContinue
if ($null -eq $mongo) {
    throw "MongoDB service not found. Install MongoDB Community, or point MONGO_URL at another server."
}
if ($mongo.Status -ne "Running") {
    throw "MongoDB service is not running (status: $($mongo.Status)). Start it, then retry."
}

function Test-PortBusy([int]$port) {
    $null -ne (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

function Test-WorkerRunning {
    $procs = Get-CimInstance Win32_Process -Filter "Name like '%python%'" -ErrorAction SilentlyContinue |
             Where-Object { $_.CommandLine -like '*worker.py*' }
    $null -ne $procs
}

# --- start -------------------------------------------------------------------
# Each process is skipped if it is already up, so re-running this script is safe
# and does not leave a second copy fighting for the same port.

if (Test-PortBusy 8000) {
    Write-Output "API      already running on :8000 (skipped)"
} else {
    Start-Process -FilePath $py -ArgumentList "-m","uvicorn","server:app","--port","8000" -WorkingDirectory $backend
    Write-Output "API      starting on :8000"
}

if (Test-WorkerRunning) {
    Write-Output "Worker   already running (skipped)"
} else {
    Start-Process -FilePath $py -ArgumentList "worker.py" -WorkingDirectory $backend
    Write-Output "Worker   starting"
}

if (Test-PortBusy 3000) {
    Write-Output "Frontend already running on :3000 (skipped)"
} else {
    Start-Process -FilePath "npm.cmd" -ArgumentList "start" -WorkingDirectory $frontend
    Write-Output "Frontend starting on :3000"
}

# --- health ------------------------------------------------------------------

Write-Output ""
Write-Output "Waiting for the API to answer..."
$ok = $false
foreach ($i in 1..30) {
    Start-Sleep -Seconds 1
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/" -TimeoutSec 2
        if ($r.status -eq "ok") { $ok = $true; break }
    } catch { }
}

Write-Output ""
if ($ok) { Write-Output "API      http://localhost:8000/api/  [ok]" }
else     { Write-Output "API      http://localhost:8000/api/  [no response yet - check its window]" }
Write-Output "Frontend http://localhost:3000  (webpack takes ~30s on a cold start)"
Write-Output "Worker   check its window for 'ready, handlers: [export, transcribe]'"
