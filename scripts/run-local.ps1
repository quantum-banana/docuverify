#requires -Version 5.1

[CmdletBinding()]
param(
    [string]$BackendAddress,
    [int]$BackendPort = 0,
    [string]$FrontendAddress = '127.0.0.1',
    [int]$FrontendPort = 5173,
    [switch]$NoReload
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot '_common.ps1')

function Stop-DocuVerifyProcessTree {
    param(
        [System.Diagnostics.Process]$ManagedProcess,
        [string]$Label
    )

    if ($null -eq $ManagedProcess) {
        return
    }

    try { $ManagedProcess.Refresh() } catch { return }
    if ($ManagedProcess.HasExited) {
        return
    }

    Write-Host "Stopping $Label..." -ForegroundColor Yellow
    $taskKillCommand = Get-Command taskkill.exe -CommandType Application -ErrorAction SilentlyContinue
    if ($null -ne $taskKillCommand) {
        # Native stderr must not become a terminating NativeCommandError under
        # the script-wide Stop policy; a raced exit or a force-required process
        # is expected here and is handled below.
        try {
            & $taskKillCommand.Source /PID $ManagedProcess.Id /T 2>$null | Out-Null
        }
        catch { }
        Start-Sleep -Milliseconds 750
        try { $ManagedProcess.Refresh() } catch { return }

        if (-not $ManagedProcess.HasExited) {
            try {
                & $taskKillCommand.Source /PID $ManagedProcess.Id /T /F 2>$null | Out-Null
            }
            catch { }
            Start-Sleep -Milliseconds 250
            try { $ManagedProcess.Refresh() } catch { return }
        }
    }

    if (-not $ManagedProcess.HasExited) {
        Stop-Process -Id $ManagedProcess.Id -Force -ErrorAction SilentlyContinue
    }
}

$projectRoot = Get-DocuVerifyProjectRoot
$backendDirectory = Join-Path $projectRoot 'backend'
$frontendDirectory = Join-Path $projectRoot 'frontend'
$virtualEnvironmentPython = Join-Path $projectRoot '.venv\Scripts\python.exe'

Import-DocuVerifyEnvironment

if ([string]::IsNullOrWhiteSpace($BackendAddress)) {
    $BackendAddress = [Environment]::GetEnvironmentVariable('DOCUVERIFY_BACKEND_HOST', 'Process')
    if ([string]::IsNullOrWhiteSpace($BackendAddress)) {
        $BackendAddress = '127.0.0.1'
    }
}
if ($BackendPort -eq 0) {
    $configuredBackendPort = [Environment]::GetEnvironmentVariable('DOCUVERIFY_BACKEND_PORT', 'Process')
    if ([string]::IsNullOrWhiteSpace($configuredBackendPort)) {
        $BackendPort = 8000
    }
    elseif (-not [int]::TryParse($configuredBackendPort, [ref]$BackendPort)) {
        throw 'DOCUVERIFY_BACKEND_PORT must be an integer.'
    }
}
if ($BackendPort -lt 1 -or $BackendPort -gt 65535 -or $FrontendPort -lt 1 -or $FrontendPort -gt 65535) {
    throw 'BackendPort and FrontendPort must be between 1 and 65535.'
}

# Keep Vite's same-origin /api proxy aligned with the backend selected for this
# run, including explicit -BackendPort overrides. Wildcard bind addresses are
# not connectable targets, so the local proxy uses loopback for those cases.
$frontendProxyHost = $(if ($BackendAddress -in @('0.0.0.0', '::', '[::]')) { '127.0.0.1' } else { $BackendAddress })
$frontendProxyTarget = "http://$frontendProxyHost`:$BackendPort"
[Environment]::SetEnvironmentVariable('VITE_API_PROXY_TARGET', $frontendProxyTarget, 'Process')

if (-not (Test-Path -LiteralPath $virtualEnvironmentPython -PathType Leaf)) {
    throw 'The project virtual environment is missing. Run .\scripts\bootstrap-windows.ps1 first.'
}
if (-not (Test-Path -LiteralPath (Join-Path $backendDirectory 'app\main.py') -PathType Leaf)) {
    throw 'backend\app\main.py is missing.'
}
if (-not (Test-Path -LiteralPath (Join-Path $frontendDirectory 'package.json') -PathType Leaf)) {
    throw 'frontend\package.json is missing.'
}

$npmCommand = Get-Command npm.cmd -CommandType Application -ErrorAction SilentlyContinue
if ($null -eq $npmCommand) {
    throw 'npm.cmd is missing. Install Node.js with npm, then rerun bootstrap.'
}

$backendArguments = @(
    '-m', 'uvicorn', 'backend.app.main:app',
    '--host', $BackendAddress,
    '--port', $BackendPort.ToString()
)
if (-not $NoReload) {
    $backendArguments += '--reload'
}

$frontendArguments = @(
    'run', 'dev', '--',
    '--host', $FrontendAddress,
    '--port', $FrontendPort.ToString()
)

$backendProcess = $null
$frontendProcess = $null
$scriptExitCode = 0

try {
    Write-Host 'Starting DocuVerify backend and frontend...' -ForegroundColor Green
    $backendProcess = Start-Process -FilePath $virtualEnvironmentPython -ArgumentList $backendArguments -WorkingDirectory $projectRoot -PassThru -NoNewWindow
    $frontendProcess = Start-Process -FilePath $npmCommand.Source -ArgumentList $frontendArguments -WorkingDirectory $frontendDirectory -PassThru -NoNewWindow

    Write-Host "Frontend: http://$FrontendAddress`:$FrontendPort" -ForegroundColor Cyan
    Write-Host "Backend:  http://$BackendAddress`:$BackendPort" -ForegroundColor Cyan
    Write-Host "API proxy: $frontendProxyTarget" -ForegroundColor Cyan
    Write-Host "API docs: http://$BackendAddress`:$BackendPort/api/docs" -ForegroundColor Cyan
    Write-Host 'Press Ctrl+C to stop both services.'

    while ($true) {
        Start-Sleep -Milliseconds 500
        $backendProcess.Refresh()
        $frontendProcess.Refresh()

        if ($backendProcess.HasExited) {
            $backendCode = $backendProcess.ExitCode
            if ($null -eq $backendCode) { $backendCode = 1 }
            Write-Host "Backend exited with code $backendCode." -ForegroundColor Red
            $scriptExitCode = $(if ($backendCode -eq 0) { 1 } else { $backendCode })
            break
        }
        if ($frontendProcess.HasExited) {
            $frontendCode = $frontendProcess.ExitCode
            if ($null -eq $frontendCode) { $frontendCode = 1 }
            Write-Host "Frontend exited with code $frontendCode." -ForegroundColor Red
            $scriptExitCode = $(if ($frontendCode -eq 0) { 1 } else { $frontendCode })
            break
        }
    }
}
finally {
    Stop-DocuVerifyProcessTree -ManagedProcess $frontendProcess -Label 'frontend'
    Stop-DocuVerifyProcessTree -ManagedProcess $backendProcess -Label 'backend'
}

exit $scriptExitCode
