#Requires -Version 5.1
<#
.SYNOPSIS
    Автозапуск бота MAX на домашнем ПК: задача в Планировщике Windows.

.DESCRIPTION
    Регистрирует задачу `ZarubaMaxBot`, которая при входе в систему (или при
    включении ПК с -AsSystem) запускает `run_bot_loop.ps1` в скрытом окне.
    Параметры задачи: без ограничения времени выполнения, не останавливаться при
    переходе на батарею, второй экземпляр не запускать.

    Администратор не нужен: задача создаётся для текущего пользователя.
    `-AsSystem` (старт до входа в систему) — только из консоли администратора.

.PARAMETER TaskName
    Имя задачи. По умолчанию `ZarubaMaxBot`.

.PARAMETER AsSystem
    Запускать от SYSTEM при включении ПК, без входа в систему. Нужны права
    администратора; у SYSTEM нет профиля пользователя и его учётных данных git.

.PARAMETER NoStart
    Только зарегистрировать: не запускать бота сразу после установки.

.PARAMETER Remove
    Остановить бота, снять задачу и выйти.

.PARAMETER PreventSleep
    Дополнительно выключить сон от сети (`powercfg /change standby-timeout-ac 0`).
    Нужны права администратора.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy/local-pc/install_autostart.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy/local-pc/install_autostart.ps1 -Remove
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'ZarubaMaxBot',
    [string]$RepoRoot = '',
    [switch]$AsSystem,
    [switch]$NoStart,
    [switch]$Remove,
    [switch]$PreventSleep
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrEmpty($RepoRoot)) {
    # `$PSScriptRoot` в блоке param() на Windows PowerShell 5.1 ещё пуст —
    # путь по умолчанию считается здесь, а не в объявлении параметра.
    $RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}

$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$loopScript = Join-Path $RepoRoot 'deploy\local-pc\run_bot_loop.ps1'
$python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$envFile = Join-Path $RepoRoot '.env'
$botLog = Join-Path $RepoRoot 'logs\max_bot.log'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)

function Write-Step {
    # [AllowEmptyString]: пустая строка — это разделитель в отчёте, а Mandatory
    # в PowerShell 5.1 отвергает '' и валит весь скрипт на последнем шаге.
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Text)
    Write-Host $Text
}

function Stop-TaskAndBot {
    # Планировщик гасит только сам powershell: дочерний бот остался бы жить и
    # отвечать в MAX параллельно с новым — два ответа на одно сообщение.
    # `run_bot_loop.ps1 -Stop` снимает процесс по записанному PID.
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        if ($task.State -eq 'Running') {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 1
    }
    if (Test-Path -LiteralPath $loopScript) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $loopScript -Stop -RepoRoot $RepoRoot
    }
}

function Get-AcStandbySeconds {
    # `powercfg` печатает локализованные подписи, но значения — всегда `0x…`;
    # первых два числа в блоке STANDBYIDLE: от сети и от батареи.
    $text = (powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE) -join "`n"
    $values = [regex]::Matches($text, '0x[0-9a-fA-F]{8}')
    if ($values.Count -lt 2) { return $null }
    return [Convert]::ToInt32($values[0].Value, 16)
}

if ($Remove) {
    if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
        Write-Step "задача $TaskName не зарегистрирована — удалять нечего"
        exit 0
    }
    Stop-TaskAndBot
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Step "задача $TaskName удалена, бот остановлен"
    exit 0
}
if (-not (Test-Path -LiteralPath $loopScript)) {
    throw "не найден $loopScript — запустите скрипт из репозитория проекта"
}
if (-not (Test-Path -LiteralPath $python)) {
    throw "не найден $python — создайте окружение (.venv) и поставьте зависимости (README)"
}

$hasToken = $false
if (Test-Path -LiteralPath $envFile) {
    $found = Select-String -LiteralPath $envFile -Pattern '^\s*ZARUBA_MAX_BOT_TOKEN\s*=\s*\S' -ErrorAction SilentlyContinue
    $hasToken = [bool]$found
}
if (-not $hasToken) {
    Write-Step "ВНИМАНИЕ: в .env нет строки ZARUBA_MAX_BOT_TOKEN=..."
    Write-Step ("         Бот не запустится. Впишите токен и запустите: Start-ScheduledTask -TaskName {0}" -f $TaskName)
}

$standby = Get-AcStandbySeconds
if ($null -eq $standby) {
    Write-Step 'сон: настройку powercfg прочитать не удалось — проверьте «Сон после» вручную'
}
elseif ($standby -eq 0) {
    Write-Step 'сон от сети: выключен (0 = никогда) — ПК не заснёт вместе с ботом'
}
else {
    Write-Step ("сон от сети: через {0} с — ПК уснёт и бот замолчит" -f $standby)
    if ($PreventSleep -and $isAdmin) {
        & powercfg /change standby-timeout-ac 0
        Write-Step '  сон от сети выключен (powercfg /change standby-timeout-ac 0)'
    }
    elseif ($PreventSleep) {
        Write-Step '  -PreventSleep требует прав администратора: запустите из консоли администратора'
    }
    else {
        Write-Step '  выключить: powercfg /change standby-timeout-ac 0 (или флаг -PreventSleep)'
    }
}

if ($AsSystem -and -not $isAdmin) {
    throw '-AsSystem требует консоли администратора: Start-Process powershell -Verb RunAs'
}

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -RepoRoot "{1}"' -f $loopScript, $RepoRoot) `
    -WorkingDirectory $RepoRoot

if ($AsSystem) {
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $triggerText = 'при включении ПК (до входа в систему)'
}
else {
    $me = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $me
    $principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
    $triggerText = 'при входе в систему'
}

# Без ограничения по времени: бот живёт сутками, а лимит по умолчанию — 72 часа.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Stop-TaskAndBot
try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Force `
        -Description 'Бот MAX чемпионата «ЗАРУБА»: long polling 24/7 (docs/09 §9.12)' | Out-Null
}
catch {
    throw ("не удалось зарегистрировать задачу: {0}`nЕсли задача создана от администратора — запустите этот скрипт из консоли администратора." -f $_.Exception.Message)
}

Write-Step ("зарегистрировано: {0} — запуск {1}, пользователь {2}" -f $TaskName, $triggerText, $principal.UserId)

if ($NoStart) {
    Write-Step ("запуск пропущен (-NoStart): Start-ScheduledTask -TaskName {0}" -f $TaskName)
    exit 0
}
if (-not $hasToken) {
    Write-Step 'бот не запущен: сначала впишите ZARUBA_MAX_BOT_TOKEN в .env'
    exit 0
}

Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 12
$state = (Get-ScheduledTask -TaskName $TaskName).State
# Два процесса — норма: `.venv\Scripts\python.exe` здесь лаунчер, он запускает
# настоящий интерпретатор дочерним процессом.
$running = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { "$($_.CommandLine)" -like '*zaruba.max_bot*' })
Write-Step ''
Write-Step ("состояние задачи: {0}; процессов бота: {1} (лаунчер + интерпретатор)" -f $state, $running.Count)

if (Test-Path -LiteralPath $botLog) {
    Write-Step '--- последние строки logs\max_bot.log ---'
    Get-Content -LiteralPath $botLog -Tail 5 -Encoding UTF8 | ForEach-Object { Write-Step "  $_" }
}
else {
    Write-Step 'logs\max_bot.log пока пуст: бот пишет в него только события (ответы, обрывы связи)'
}
Write-Step ''
Write-Step 'проверка в любой момент:'
Write-Step ("  Get-ScheduledTask -TaskName {0} | Select-Object TaskName,State" -f $TaskName)
Write-Step '  Get-Content logs\max_bot.log -Tail 20 -Encoding UTF8'
Write-Step '  Get-Content logs\max_bot_loop.log -Tail 20 -Encoding UTF8'
Write-Step 'остановить и убрать из автозапуска:'
Write-Step '  powershell -ExecutionPolicy Bypass -File deploy\local-pc\install_autostart.ps1 -Remove'

