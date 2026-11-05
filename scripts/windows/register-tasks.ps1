# Registers the two Task Scheduler tasks that keep the bot running on the mini PC.
# Run once from an elevated PowerShell:  .\register-tasks.ps1 -RepoPath /home/<you>/china-trip-bot
param(
    [Parameter(Mandatory = $true)][string]$RepoPath,   # path inside WSL
    [string]$Distro = "Ubuntu",
    [string]$NightlyAt = "03:00"                       # Windows local time
)

$ErrorActionPreference = "Stop"
$user = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited

function Register-WslTask($name, $script, $trigger, $settings) {
    $action = New-ScheduledTaskAction -Execute "wsl.exe" `
        -Argument "-d $Distro -- bash -lc '$RepoPath/scripts/$script'"
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Force | Out-Null
    Write-Host "registered $name"
}

# Nightly: wake the PC, run once, give up after an hour, catch up if the PC was off.
$nightly = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
Register-WslTask "tripbot-nightly" "run-nightly.sh" `
    (New-ScheduledTaskTrigger -Daily -At $NightlyAt) $nightly

# Bot listener: start at boot, never time out, restart if it stops.
$bot = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-WslTask "tripbot-bot" "run-bot.sh" (New-ScheduledTaskTrigger -AtStartup) $bot

Write-Host "Start the listener now with: Start-ScheduledTask -TaskName tripbot-bot"
