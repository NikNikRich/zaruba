#Requires -Version 5.1
<#
.SYNOPSIS
    Круглосуточный цикл бота MAX: перезапуск после падения, свежие данные, ротация лога.

.DESCRIPTION
    Запускает `.venv\Scripts\python.exe -m zaruba.max_bot --polling` дочерним
    процессом и следит за ним:

      * падение (обрыв домашнего интернета, исключение, код 1) — пауза 5 секунд
        и новый запуск, так что ночной разрыв связи не требует человека;
      * раз в -RefreshMinutes минут — `git pull --ff-only` и (если задан
        ZARUBA_SHEETS_MAP) `python -m zaruba.sync_tables --from-sheets`;
        бот перечитывает контент на каждом цикле опроса, перезапуск не нужен;
      * лог `logs/max_bot.log` не растёт бесконечно: после -LogMaxMB мегабайт
        файл проворачивается в `.1`, `.2`, … Для поворота бот на секунду
        останавливается — открытый файл на Windows не переименовать.

    Токен и прочие переменные читаются из `.env` в корне репозитория: у задачи
    Планировщика нет ни консоли, ни профиля пользователя, поэтому `.env`
    разбирается здесь и передаётся дочерним процессам через окружение.

.PARAMETER RepoRoot
    Корень репозитория. По умолчанию — два уровня выше этой папки.

.PARAMETER PythonPath
    Интерпретатор. По умолчанию `.venv\Scripts\python.exe`, иначе `python` из PATH.

.PARAMETER Stop
    Остановить бота по `logs/max_bot.pid` и выйти: так снимается процесс,
    оставшийся после аварийной остановки задачи.

.PARAMETER VkInterval
    Период опроса постов ВКонтакте, секунды. Процесс мониторинга поднимается
    вторым рядом с ботом, только если в окружении задан ZARUBA_VK_TOKEN.

.PARAMETER SkipVk
    Не запускать мониторинг ВК: токен есть, а уведомления сейчас не нужны.

.PARAMETER Once
    Один запуск бота и выход (диагностика): цикл не перезапускает его.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy/local-pc/run_bot_loop.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy/local-pc/run_bot_loop.ps1 -Stop
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = '',
    [string]$PythonPath = '',
    [int]$RestartDelaySeconds = 5,
    [int]$RefreshMinutes = 30,
    [int]$LogMaxMB = 5,
    [int]$LogKeepFiles = 3,
    [double]$PollTimeout = 30,
    [double]$PollPause = 1,
    [int]$VkInterval = 60,
    [switch]$SkipRefresh,
    [switch]$SkipVk,
    [switch]$Stop,
    [switch]$Once
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrEmpty($RepoRoot)) {
    # `$PSScriptRoot` в блоке param() на Windows PowerShell 5.1 ещё пуст —
    # путь по умолчанию считается здесь, а не в объявлении параметра.
    $RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}

$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$logDir = Join-Path $RepoRoot 'logs'
$botLog = Join-Path $logDir 'max_bot.log'
$loopLog = Join-Path $logDir 'max_bot_loop.log'
$pidFile = Join-Path $logDir 'max_bot.pid'
# Отдельные файлы у мониторинга ВК: строки двух процессов в одном журнале
# не разложить, а PID нужен свой — иначе `-Stop` снимет не тот процесс.
$vkLog = Join-Path $logDir 'vk_watch.log'
$vkPidFile = Join-Path $logDir 'vk_watch.pid'
$maxBytes = [int64]$LogMaxMB * 1MB

if (-not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

function Invoke-LogRotation {
    # `max_bot.log` → `.1`, прежний `.1` → `.2` и так до -LogKeepFiles.
    # Возвращает $true, если файл пришлось повернуть.
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][int64]$MaxBytes,
        [Parameter(Mandatory)][int]$Keep
    )
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    if ((Get-Item -LiteralPath $Path).Length -lt $MaxBytes) { return $false }
    try {
        for ($i = $Keep - 1; $i -ge 1; $i--) {
            $older = '{0}.{1}' -f $Path, $i
            if (Test-Path -LiteralPath $older) {
                Move-Item -LiteralPath $older -Destination ('{0}.{1}' -f $Path, ($i + 1)) -Force
            }
        }
        Move-Item -LiteralPath $Path -Destination ('{0}.1' -f $Path) -Force
        return $true
    }
    catch {
        # Блокировка файла (антивирус, индексатор) не повод останавливать бота:
        # лог важнее только тогда, когда бот отвечает. Поворот повторится позже.
        Write-Host ('{0}: лог не повёрнут — {1}' -f $Path, $_.Exception.Message)
        return $false
    }
}

function Write-LoopLog {
    # Журнал супервизора отдельно от журнала бота: строки бота пишет Python в
    # `max_bot.log`, а здесь — перезапуски и обновления, которых в том логе нет.
    param([Parameter(Mandatory)][string]$Message)
    $null = Invoke-LogRotation -Path $loopLog -MaxBytes $maxBytes -Keep $LogKeepFiles
    $line = '{0} {1}' -f (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'), $Message
    Write-Host $line
    try {
        Add-Content -LiteralPath $loopLog -Value $line -Encoding UTF8
    }
    catch {
        # Диск заполнен или файл занят: строку уже показали, а падать из-за
        # журнала супервизор права не имеет — иначе бот останется без надзора.
        Write-Host ('журнал не записан: {0}' -f $_.Exception.Message)
    }
}

function Import-DotEnv {
    # `.env` → окружение процесса. Уже заданная переменная важнее файла:
    # временная правка `$env:ZARUBA_...` должна побеждать значение из файла.
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return 0 }
    $applied = 0
    foreach ($raw in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $line = $raw.Trim()
        if ($line.Length -eq 0 -or $line.StartsWith('#')) { continue }
        $eq = $line.IndexOf('=')
        if ($eq -lt 1) { continue }
        $name = $line.Substring(0, $eq).Trim()
        $value = $line.Substring($eq + 1).Trim()
        if ($value.Length -ge 2) {
            $pair = '{0}{1}' -f $value[0], $value[$value.Length - 1]
            if ($pair -eq '""' -or $pair -eq "''") {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        # Пустое значение в `.env` не должно затирать переменную окружения.
        if ([string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($name))) {
            [Environment]::SetEnvironmentVariable($name, $value)
            $applied++
        }
    }
    return $applied
}
function Stop-ProcessTree {
    # `taskkill /T` — потому что `.venv\Scripts\python.exe` в этом окружении
    # лаунчер (`py.exe`): настоящий интерпретатор работает дочерним процессом,
    # и `Stop-Process` по одному PID оставил бы его жить и отвечать в MAX.
    param([Parameter(Mandatory)][int]$ProcessId)
    $null = & taskkill.exe /PID $ProcessId /T /F 2>&1
    return ($LASTEXITCODE -eq 0)
}

function Get-BotPid {
    if (-not (Test-Path -LiteralPath $pidFile)) { return 0 }
    $text = Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    $value = 0
    if ([int]::TryParse("$text".Trim(), [ref]$value)) { return $value }
    return 0
}

function Stop-StaleBot {
    # `Stop-ScheduledTask` гасит только powershell, дочерний бот переживает
    # остановку и опрашивает MAX параллельно с новым — это два ответа на одно
    # сообщение. Снимаем по PID-файлу, но только если в командной строке
    # действительно `zaruba.max_bot`: чужой процесс с тем же PID не трогаем.
    $stale = Get-BotPid
    if ($stale -le 0) { return }
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$stale" -ErrorAction SilentlyContinue
    if ($proc -and ("$($proc.CommandLine)" -like '*zaruba.max_bot*')) {
        $null = Stop-ProcessTree -ProcessId $stale
        Write-LoopLog "снят прошлый бот: PID $stale (остался после аварийной остановки)"
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}

function Stop-StaleVkWatch {
    # Тот же риск, что у бота: остановка задачи гасит только powershell, а
    # дочерний мониторинг продолжал бы слать уведомления параллельно с новым.
    if (-not (Test-Path -LiteralPath $vkPidFile)) { return }
    $text = Get-Content -LiteralPath $vkPidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    $stale = 0
    if ([int]::TryParse("$text".Trim(), [ref]$stale) -and $stale -gt 0) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$stale" -ErrorAction SilentlyContinue
        if ($proc -and ("$($proc.CommandLine)" -like '*zaruba.vk_watch*')) {
            $null = Stop-ProcessTree -ProcessId $stale
            Write-LoopLog "снят прошлый мониторинг VK: PID $stale"
        }
    }
    Remove-Item -LiteralPath $vkPidFile -Force -ErrorAction SilentlyContinue
}

function Invoke-DataRefresh {
    # Обновление данных. Любая ошибка — строка в лог и продолжение работы:
    # сеть отвалилась, данные приедут в следующий раз, бот падать не должен.
    Write-LoopLog 'обновление данных: старт'
    try {
        $git = Get-Command git.exe -ErrorAction SilentlyContinue
        if ($git -and (Test-Path -LiteralPath (Join-Path $RepoRoot '.git'))) {
            # `--untracked-files=no`: неотслеживаемый файл (`.env`, `.clinerules`)
            # быстрому перемотыванию не мешает, а без флага pull не работал бы
            # никогда. Если чужая правка всё же столкнётся с приходящей, git
            # откажется сам — это строка в логе, а не испорченное дерево.
            $dirty = & git.exe -C $RepoRoot status --porcelain --untracked-files=no 2>&1
            if ("$dirty".Trim().Length -gt 0) {
                # Правка в дереве — хозяйская: pull поверх неё устроит конфликт
                # посреди работы, а не «обновит данные».
                Write-LoopLog 'git: в дереве локальные изменения — pull пропущен'
            }
            else {
                $pulled = & git.exe -C $RepoRoot -c "safe.directory=$RepoRoot" `
                    -c credential.interactive=never pull --ff-only 2>&1
                $code = $LASTEXITCODE
                foreach ($line in $pulled) {
                    if ("$line".Trim().Length -gt 0) { Write-LoopLog "git: $line" }
                }
                Write-LoopLog "git pull --ff-only: код $code"
            }
        }
        else {
            Write-LoopLog 'git: не найден — обновление репозитория пропущено'
        }

        if ([string]::IsNullOrEmpty($env:ZARUBA_SHEETS_MAP)) {
            Write-LoopLog 'таблицы: ZARUBA_SHEETS_MAP не задан — выгрузка пропущена'
        }
        else {
            $synced = & $PythonPath -m zaruba.sync_tables --from-sheets 2>&1
            $code = $LASTEXITCODE
            foreach ($line in $synced) {
                if ("$line".Trim().Length -gt 0) { Write-LoopLog "таблицы: $line" }
            }
            Write-LoopLog "sync_tables --from-sheets: код $code"
        }
    }
    catch {
        Write-LoopLog "обновление данных не удалось: $($_.Exception.Message)"
    }
}
function Start-Bot {
    # Один лог на stdout и stderr: `Start-Process` не умеет писать два потока в
    # один файл, а `cmd /c` с `2>&1` умеет. Числа уходят в аргументы с точкой —
    # argparse не поймёт «0,5» из русской локали.
    $invariant = [System.Globalization.CultureInfo]::InvariantCulture
    $timeout = $PollTimeout.ToString('0.###', $invariant)
    $pause = $PollPause.ToString('0.###', $invariant)
    $inner = '"{0}" -m zaruba.max_bot --polling --timeout {1} --pause {2} 1>>"{3}" 2>&1' -f `
        $PythonPath, $timeout, $pause, $botLog
    $proc = Start-Process -FilePath $env:ComSpec -ArgumentList ('/d /c "' + $inner + '"') `
        -WorkingDirectory $RepoRoot -WindowStyle Hidden -PassThru
    Set-Content -LiteralPath $pidFile -Value $proc.Id -Encoding ASCII
    return $proc
}

function Start-VkWatch {
    # Второй процесс рядом с ботом, а не вторая задача Планировщика: второй
    # супервизор означал бы ещё один `git pull` и ещё один набор логов.
    $inner = '"{0}" -m zaruba.vk_watch --loop --interval {1} 1>>"{2}" 2>&1' -f `
        $PythonPath, $VkInterval, $vkLog
    $proc = Start-Process -FilePath $env:ComSpec -ArgumentList ('/d /c "' + $inner + '"') `
        -WorkingDirectory $RepoRoot -WindowStyle Hidden -PassThru
    Set-Content -LiteralPath $vkPidFile -Value $proc.Id -Encoding ASCII
    return $proc
}

if ($Stop) {
    Stop-StaleBot
    Stop-StaleVkWatch
    Write-LoopLog 'остановка: цикл больше не запускается'
    exit 0
}

$fromEnv = Import-DotEnv -Path (Join-Path $RepoRoot '.env')

if ($PythonPath.Length -eq 0) {
    $venvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPython) {
        $PythonPath = $venvPython
    }
    else {
        $found = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($found) { $PythonPath = $found.Source } else { $PythonPath = 'python' }
    }
}

Write-LoopLog "старт цикла: $RepoRoot; переменных из .env: $fromEnv; python: $PythonPath"

if (-not (Test-Path -LiteralPath $PythonPath)) {
    Write-LoopLog "ОШИБКА: интерпретатор не найден: $PythonPath — создайте .venv (docs/09 §9.12)"
    exit 2
}

# Без UTF-8 строки бота уезжали бы в лог в cp1251, а без PYTHONUNBUFFERED
# лежали бы в буфере, и лог молчал бы минутами.
$env:PYTHONUNBUFFERED = '1'
$env:PYTHONUTF8 = '1'
# git без консоли не должен ждать пароль: иначе pull висит до остановки задачи.
$env:GIT_TERMINAL_PROMPT = '0'

$null = Invoke-LogRotation -Path $loopLog -MaxBytes $maxBytes -Keep $LogKeepFiles
Stop-StaleBot
Stop-StaleVkWatch

if ([string]::IsNullOrEmpty($env:ZARUBA_MAX_BOT_TOKEN)) {
    # Перезапуск каждые 5 секунд тут вреден: бот умирал бы с кодом 1 и писал
    # одно и то же. Пусть цикл завершится с кодом 3 — это видно в Планировщике.
    Write-LoopLog 'ОШИБКА: ZARUBA_MAX_BOT_TOKEN не задан — впишите токен в .env (docs/09 §9.12)'
    exit 3
}
$nextRefresh = Get-Date
$bot = $null
$vk = $null
# Мониторинг ВК — второй процесс рядом с ботом. Без токена он не нужен, а в
# диагностическом `-Once` второй бесконечный цикл только мешал бы выходу.
$vkToken = -not [string]::IsNullOrEmpty($env:ZARUBA_VK_TOKEN)
$watchVk = $vkToken -and (-not $Once) -and (-not $SkipVk)
if (-not $vkToken) {
    Write-LoopLog 'мониторинг VK: ZARUBA_VK_TOKEN не задан — процесс не запускается (docs/09 §9.13)'
}
elseif (-not $watchVk) {
    Write-LoopLog 'мониторинг VK: пропущен (-Once или -SkipVk)'
}
try {
    while ($true) {
        if (-not $SkipRefresh -and (Get-Date) -ge $nextRefresh) {
            Invoke-DataRefresh
            $nextRefresh = (Get-Date).AddMinutes($RefreshMinutes)
        }

        $null = Invoke-LogRotation -Path $botLog -MaxBytes $maxBytes -Keep $LogKeepFiles
        Write-LoopLog ('запуск бота: {0} -m zaruba.max_bot --polling' -f $PythonPath)
        $bot = Start-Bot
        $reason = 'exit'

        if ($watchVk -and ($null -eq $vk -or $vk.HasExited)) {
            $vk = Start-VkWatch
            Write-LoopLog ('запуск мониторинга VK: -m zaruba.vk_watch --loop --interval {0}' -f $VkInterval)
        }

        # Секунда вместо минуты: обещание «перезапуск через 5 секунд» должно
        # выполняться, а не превращаться в «через 5 секунд плюс шаг проверки».
        while (-not $bot.HasExited) {
            Start-Sleep -Seconds 1
            $bot.Refresh()
            if ($bot.HasExited) { break }
            if ((Test-Path -LiteralPath $botLog) -and
                ((Get-Item -LiteralPath $botLog).Length -gt $maxBytes)) {
                $reason = 'rotate'
                break
            }
            if ($watchVk -and $vk.HasExited) {
                # Падение мониторинга не повод рвать сессию бота, который в этот
                # момент отвечает людям: перезапускаем только мониторинг.
                Write-LoopLog ("мониторинг VK завершился, код {0}; перезапуск" -f $vk.ExitCode)
                $vk = Start-VkWatch
            }
            if ($watchVk -and (Test-Path -LiteralPath $vkLog) -and
                ((Get-Item -LiteralPath $vkLog).Length -gt $maxBytes)) {
                $null = Stop-ProcessTree -ProcessId $vk.Id
                $null = $vk.WaitForExit(15000)
                $null = Invoke-LogRotation -Path $vkLog -MaxBytes $maxBytes -Keep $LogKeepFiles
                Write-LoopLog "лог VK превысил $LogMaxMB МБ — поворот и перезапуск мониторинга"
                $vk = Start-VkWatch
            }
            if (-not $SkipRefresh -and (Get-Date) -ge $nextRefresh) {
                # Контент бот перечитывает на каждом цикле опроса (`load_latest`),
                # поэтому свежие файлы подхватываются без перезапуска.
                Invoke-DataRefresh
                $nextRefresh = (Get-Date).AddMinutes($RefreshMinutes)
            }
        }

        if ($reason -eq 'rotate') {
            $null = Stop-ProcessTree -ProcessId $bot.Id
            $null = $bot.WaitForExit(15000)
            $null = Invoke-LogRotation -Path $botLog -MaxBytes $maxBytes -Keep $LogKeepFiles
            Write-LoopLog "лог превысил $LogMaxMB МБ — поворот и перезапуск бота"
            continue
        }

        $code = $bot.ExitCode
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
        if ($Once) {
            Write-LoopLog "бот завершился, код $code (-Once: выход)"
            exit $code
        }
        Write-LoopLog "бот завершился, код $code; перезапуск через $RestartDelaySeconds с"
        Start-Sleep -Seconds $RestartDelaySeconds
    }
}
finally {
    if ($bot -and -not $bot.HasExited) {
        $null = Stop-ProcessTree -ProcessId $bot.Id
    }
    if ($vk -and -not $vk.HasExited) {
        $null = Stop-ProcessTree -ProcessId $vk.Id
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $vkPidFile -Force -ErrorAction SilentlyContinue
    Write-LoopLog 'цикл остановлен'
}



