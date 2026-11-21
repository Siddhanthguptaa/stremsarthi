# ============================================================
# Stream Sarthi – WSL2 Post-Reboot Setup (Step 2 of 2)
# Run in an ELEVATED (Admin) PowerShell AFTER the reboot.
#
#   Set-ExecutionPolicy -Scope Process Bypass
#   & "C:\Users\suresh\OneDrive\Desktop\stremsarthi\scripts\win_post_reboot.ps1"
# ============================================================

$ErrorActionPreference = "Stop"
$ProjectRoot = "C:\Users\suresh\OneDrive\Desktop\stremsarthi"
$WslUser = "suresh"           # Linux username to create inside Ubuntu

Write-Host "`n== Ensuring WSL kernel is up to date ==" -ForegroundColor Cyan
wsl --update

Write-Host "`n== Setting WSL default version to 2 ==" -ForegroundColor Cyan
wsl --set-default-version 2

Write-Host "`n== Installing Ubuntu 24.04 (this takes several minutes) ==" -ForegroundColor Cyan

# Check if Ubuntu-24.04 already exists
$distros = wsl --list --quiet 2>&1 | ForEach-Object { $_ -replace "`0", "" }
if ($distros -match "Ubuntu-24.04") {
    Write-Host "  Ubuntu-24.04 already installed, skipping."
} else {
    # Install without prompting for a user (--no-launch so we can configure next)
    wsl --install -d Ubuntu-24.04 --no-launch
    Write-Host "  Ubuntu-24.04 installed."
}

Write-Host "`n== Creating Linux user '$WslUser' and configuring Ubuntu ==" -ForegroundColor Cyan

# This runs as root inside Ubuntu to create the user, then do first-time apt setup
$setupScript = @"
#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

echo '-- apt update --'
apt-get update -qq

echo '-- creating user $WslUser --'
if ! id '$WslUser' &>/dev/null; then
    adduser --gecos '' --disabled-password '$WslUser'
    echo '$WslUser ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/$WslUser
    chmod 440 /etc/sudoers.d/$WslUser
    echo 'User $WslUser created with passwordless sudo.'
else
    echo 'User $WslUser already exists.'
fi

echo '-- setting default WSL user --'
printf '[user]\ndefault=$WslUser\n' > /etc/wsl.conf

echo '-- done with first-time setup --'
"@

# Write the script into /tmp inside WSL and run it as root
$tmpScript = [System.IO.Path]::GetTempFileName() + ".sh"
$setupScript | Set-Content -Encoding utf8 $tmpScript
$wslTmp = "/tmp/wsl_firstrun.sh"
wsl -d Ubuntu-24.04 -u root -- bash -c "cat > $wslTmp" < $tmpScript
Remove-Item $tmpScript
wsl -d Ubuntu-24.04 -u root -- bash $wslTmp
wsl -d Ubuntu-24.04 -u root -- rm $wslTmp

Write-Host "`n== Writing .wslconfig for mirrored networking + 8 GB RAM ==" -ForegroundColor Cyan
$wslConfig = @"
[wsl2]
memory=8GB
processors=4
networkingMode=mirrored
dnsTunneling=true
firewall=true
autoProxy=true
"@
$wslConfigPath = "$env:USERPROFILE\.wslconfig"
if (Test-Path $wslConfigPath) {
    Write-Host "  .wslconfig already exists – appending/merging required keys."
    # Append only if [wsl2] section doesn't mention networkingMode
    $existing = Get-Content $wslConfigPath -Raw
    if ($existing -notmatch "networkingMode") {
        Add-Content $wslConfigPath "`n# Added by Stream Sarthi setup`n$wslConfig"
        Write-Host "  Appended Stream Sarthi settings."
    } else {
        Write-Host "  networkingMode already present, leaving .wslconfig as-is."
    }
} else {
    $wslConfig | Set-Content $wslConfigPath -Encoding utf8
    Write-Host "  Created .wslconfig"
}

Write-Host "`n== Adding Hyper-V firewall rules for SRT (UDP 8890) and RTMP (TCP 1935) ==" -ForegroundColor Cyan
try {
    New-NetFirewallHyperVRule -Name "StreamSarthi-SRT" -DisplayName "Stream Sarthi SRT ingest" `
        -Direction Inbound -VMCreatorId "{40E0AC32-46A5-443A-9BAB-BB901A2CC3CB}" `
        -Protocol UDP -LocalPorts 8890 -Action Allow -ErrorAction SilentlyContinue
    New-NetFirewallHyperVRule -Name "StreamSarthi-RTMP" -DisplayName "Stream Sarthi RTMP ingest" `
        -Direction Inbound -VMCreatorId "{40E0AC32-46A5-443A-9BAB-BB901A2CC3CB}" `
        -Protocol TCP -LocalPorts 1935 -Action Allow -ErrorAction SilentlyContinue
    Write-Host "  Hyper-V firewall rules created."
} catch {
    Write-Warning "Could not add Hyper-V firewall rules (Windows version may not support it): $_"
    Write-Host "  You can skip this if you won't use phone SRT ingest."
}

Write-Host "`n== Restarting WSL with the new config ==" -ForegroundColor Cyan
wsl --shutdown
Start-Sleep -Seconds 3

Write-Host "`n== Verifying WSL2 is running Ubuntu-24.04 ==" -ForegroundColor Cyan
wsl --list --verbose

Write-Host "`n== Running the project setup inside Ubuntu ==" -ForegroundColor Cyan
Write-Host "  (This installs apt packages, uv, Python 3.11, all pip deps, and MediaMTX."
Write-Host "   It will take 5-15 min depending on your connection.)" -ForegroundColor Gray

# The Windows path to the project, expressed as the WSL /mnt/c path
$wslProjectRoot = "/mnt/c/Users/suresh/OneDrive/Desktop/stremsarthi"

# Full apt + project bootstrap inside Ubuntu
$bootstrapScript = @"
#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

echo '== apt packages =='
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    curl git build-essential pkg-config \
    ffmpeg libavcodec-extra \
    libsrt-openssl-dev \
    libraqm0 libfreetype6-dev libharfbuzz-dev \
    espeak-ng \
    libchromaprint-tools \
    python3-dev \
    nodejs npm \
    ca-certificates gnupg lsb-release

echo '== Node 20 via NodeSource =='
if ! node --version 2>/dev/null | grep -q '^v20'; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
fi
node --version
npm --version

echo '== uv (Python package manager) =='
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="\$HOME/.local/bin:\$PATH"
uv --version

echo '== Python 3.11 =='
uv python install 3.11

echo '== Project setup =='
cd $wslProjectRoot
bash scripts/setup_wsl.sh

echo '== Done! Run: bash scripts/ss.sh python scripts/doctor.py =='
"@

$tmpBootstrap = [System.IO.Path]::GetTempFileName() + ".sh"
$bootstrapScript | Set-Content -Encoding utf8 $tmpBootstrap
wsl -d Ubuntu-24.04 -u $WslUser -- bash -c "cat > /tmp/ss_bootstrap.sh" < $tmpBootstrap
Remove-Item $tmpBootstrap
wsl -d Ubuntu-24.04 -u $WslUser -- bash /tmp/ss_bootstrap.sh

Write-Host "`n========================================" -ForegroundColor Green
Write-Host "  WSL2 + Ubuntu 24.04 setup complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Fill in your API keys:  notepad C:\Users\suresh\OneDrive\Desktop\stremsarthi\.env"
Write-Host "  2. Run the doctor:         wsl -d Ubuntu-24.04 -u suresh -- bash /mnt/c/Users/suresh/OneDrive/Desktop/stremsarthi/scripts/ss.sh python scripts/doctor.py"
Write-Host "  3. Fetch model weights:    wsl -d Ubuntu-24.04 -u suresh -- bash /mnt/c/Users/suresh/OneDrive/Desktop/stremsarthi/scripts/ss.sh python -m models.prepare"
Write-Host ""
