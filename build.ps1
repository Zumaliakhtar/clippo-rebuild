<#
.SYNOPSIS
  One-click Windows build for Clippo Rebuild.
.DESCRIPTION
  Run: right-click -> "Run with PowerShell" (or: powershell -ExecutionPolicy Bypass -File build.ps1)
  Produces a portable dist/ClippoRebuild/ folder: ClippoRebuild.exe + bundled ffmpeg + README.
  No admin rights needed. Requires internet (pip + ffmpeg download).
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$Dist = Join-Path $Root "dist\ClippoRebuild"
$FfmpegZip = Join-Path $env:TEMP "ffmpeg-release-essentials.zip"
$FfmpegUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

Write-Host "==> 1/5 Checking Python..." -ForegroundColor Cyan
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { throw "Python not found. Install Python 3.11+ from python.org (tick 'Add to PATH')." }
$ver = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
Write-Host "    Python $ver"

Write-Host "==> 2/5 Creating venv + installing deps..." -ForegroundColor Cyan
if (-not (Test-Path ".venv")) { & python -m venv .venv }
& .\.venv\Scripts\python -m pip install --quiet --upgrade pip
& .\.venv\Scripts\python -m pip install --quiet -r requirements.txt pyinstaller

Write-Host "==> 3/5 Downloading ffmpeg (bundled, ~80 MB)..." -ForegroundColor Cyan
$binDir = Join-Path $Dist "bin"
New-Item -ItemType Directory -Force $binDir | Out-Null
if (-not (Test-Path (Join-Path $binDir "ffmpeg.exe"))) {
    Invoke-WebRequest -Uri $FfmpegUrl -OutFile $FfmpegZip
    $tmp = Join-Path $env:TEMP "ffext"
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    Expand-Archive $FfmpegZip -DestinationPath $tmp
    $exe = Get-ChildItem -Path $tmp -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
    Copy-Item $exe.FullName (Join-Path $binDir "ffmpeg.exe")
    Remove-Item $tmp -Recurse -Force
    Remove-Item $FfmpegZip -Force
    Write-Host "    ffmpeg.exe bundled."
} else {
    Write-Host "    ffmpeg.exe already bundled, skipping download."
}

Write-Host "==> 4/5 Building ClippoRebuild.exe (PyInstaller)..." -ForegroundColor Cyan
$entry = Join-Path $Root "clippo\__main__.py"
& .\.venv\Scripts\pyinstaller --noconfirm --onedir --windowed `
    --name "ClippoRebuild" `
    --distpath (Join-Path $Root "dist") `
    --workpath (Join-Path $Root "build") `
    --collect-all "PIL" `
    $entry | Out-Null

Write-Host "==> 5/5 Copying ffmpeg next to the exe + README..." -ForegroundColor Cyan
Copy-Item (Join-Path $binDir "ffmpeg.exe") (Join-Path $Dist "ffmpeg.exe") -Force
@"
Clippo Rebuild - portable folder
================================
Run ClippoRebuild.exe. No install needed.

- ffmpeg.exe is bundled (open-source, gyan.dev build).
- Your renders go wherever you choose in the app.
- Temp files live in %TEMP%\ClippoRebuild (3 GB free space required).
"@ | Out-File -Encoding utf8 (Join-Path $Dist "README.txt")

Write-Host ""
Write-Host "DONE -> $Dist\ClippoRebuild.exe" -ForegroundColor Green
Write-Host "Zip the ClippoRebuild folder to distribute it."
