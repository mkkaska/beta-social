# Her sabah "beta-social daily" komutunu çalıştıran Windows zamanlanmış görevini kurar.
# Kullanım (proje klasöründe, PowerShell):
#   powershell -ExecutionPolicy Bypass -File scripts\register_daily_task.ps1 -Time 08:30
param(
    [string]$Time = "08:30",
    [string]$TaskName = "BetaSocialDaily"
)

$ProjectDir = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Error "Sanal ortam bulunamadı: $Python. Önce README'deki kurulum adımlarını uygula."
    exit 1
}
$LogDir = Join-Path $ProjectDir "data"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Action = New-ScheduledTaskAction -Execute "cmd.exe" `
    -Argument "/c `"`"$Python`" -m beta_social daily >> `"$LogDir\daily.log`" 2>&1`"" `
    -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -Daily -At $Time
# Bilgisayar o saatte uykudaysa/kapalıysa, açılınca kaçırılan görevi çalıştır
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "Beta Hardware: Instagram + TikTok istatistik toplama ve günlük rapor" -Force | Out-Null

Write-Host "Görev kuruldu: $TaskName, her gün $Time. Kayıtlar: $LogDir\daily.log"
