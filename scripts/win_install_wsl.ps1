# ============================================================
# Stream Sarthi – WSL2 + Ubuntu 24.04 Bootstrap (Step 1 of 2)
# Run this ONCE in an ELEVATED (Admin) PowerShell window.
#
#   Set-ExecutionPolicy -Scope Process Bypass
#   & "C:\Users\suresh\OneDrive\Desktop\stremsarthi\scripts\win_install_wsl.ps1"
#
# After this script finishes it will tell you whether a reboot
# is required. If it is, reboot, then run win_post_reboot.ps1.
# ============================================================

$ErrorActionPreference = "Stop"

Write-Host "`n== Enabling Windows features ==" -ForegroundColor Cyan
$needsReboot = $false

function Enable-Feature($name) {
    $f = Get-WindowsOptionalFeature -Online -FeatureName $name
    if ($f.State -eq "Enabled") {
        Write-Host "  $name already enabled"
    } else {
        Write-Host "  Enabling $name ..."
        $r = Enable-WindowsOptionalFeature -Online -FeatureName $name -NoRestart
        if ($r.RestartNeeded) { $script:needsReboot = $true }
    }
}

Enable-Feature "Microsoft-Windows-Subsystem-Linux"
Enable-Feature "VirtualMachinePlatform"

Write-Host "`n== Setting WSL default version to 2 ==" -ForegroundColor Cyan
wsl --set-default-version 2

Write-Host "`n== Updating WSL kernel / store component ==" -ForegroundColor Cyan
try {
    wsl --update
} catch {
    Write-Warning "wsl --update failed (may need a reboot first): $_"
    $needsReboot = $true
}

if ($needsReboot) {
    Write-Host "`n!!  A REBOOT IS REQUIRED  !!" -ForegroundColor Yellow
    Write-Host "    After rebooting, open an ELEVATED PowerShell and run:" -ForegroundColor Yellow
    Write-Host "    & 'C:\Users\suresh\OneDrive\Desktop\stremsarthi\scripts\win_post_reboot.ps1'" -ForegroundColor Yellow
} else {
    Write-Host "`n== No reboot needed - proceeding to Ubuntu install ==" -ForegroundColor Cyan
    & "$PSScriptRoot\win_post_reboot.ps1"
}
