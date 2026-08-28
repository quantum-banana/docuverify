#requires -Version 5.1

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot '_common.ps1')

$projectRoot = Get-DocuVerifyProjectRoot
$backendDirectory = Join-Path $projectRoot 'backend'
$frontendDirectory = Join-Path $projectRoot 'frontend'
$virtualEnvironmentPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$failedSteps = New-Object 'System.Collections.Generic.List[string]'
$testRunRoot = $null
$previousRuntimeOverride = $null
$hadPreviousRuntimeOverride = $false
$scriptExitCode = 1

function Get-DocuVerifyTestRunName {
    return ('run-{0}-{1}' -f $PID, [Guid]::NewGuid().ToString('N'))
}

function Assert-DocuVerifyChildPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$CandidatePath,

        [Parameter(Mandatory = $true)]
        [string]$BoundaryRoot,

        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    $resolvedBoundary = [System.IO.Path]::GetFullPath($BoundaryRoot).TrimEnd('\') + '\'
    $resolvedCandidate = [System.IO.Path]::GetFullPath($CandidatePath)
    if (-not $resolvedCandidate.StartsWith($resolvedBoundary, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Description resolved outside its required boundary."
    }
    return $resolvedCandidate
}

function Remove-DocuVerifyTestRun {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RunRoot,

        [Parameter(Mandatory = $true)]
        [string]$TemporaryRoot
    )

    $resolvedRunRoot = Assert-DocuVerifyChildPath -CandidatePath $RunRoot -BoundaryRoot $TemporaryRoot -Description 'Test cleanup target'
    $runLeaf = Split-Path -Leaf $resolvedRunRoot
    if ($runLeaf -notmatch '^run-\d+-[0-9a-f]{32}$') {
        throw 'Test cleanup target does not match the DocuVerify per-run naming convention.'
    }
    if (-not (Test-Path -LiteralPath $resolvedRunRoot)) {
        return
    }
    $runItem = Get-Item -LiteralPath $resolvedRunRoot -Force
    if (($runItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw 'Test cleanup target is a reparse point; refusing recursive removal.'
    }
    Remove-Item -LiteralPath $resolvedRunRoot -Recurse -Force -ErrorAction Stop
    if (Test-Path -LiteralPath $resolvedRunRoot) {
        throw 'The unique test runtime still exists after cleanup.'
    }
}

function Invoke-VerificationStep {
    param(
        [string]$Name,
        [scriptblock]$Action
    )

    Write-Host ''
    Write-Host "== $Name ==" -ForegroundColor Cyan
    try {
        & $Action
        Write-Host "PASS: $Name" -ForegroundColor Green
    }
    catch {
        Write-Host "FAIL: $Name - $($_.Exception.Message)" -ForegroundColor Red
        $failedSteps.Add($Name)
    }
}

Import-DocuVerifyEnvironment

$previousRuntimeOverride = [Environment]::GetEnvironmentVariable('DOCUVERIFY_RUNTIME_DIR', 'Process')
$hadPreviousRuntimeOverride = $null -ne $previousRuntimeOverride

try {
    $temporaryRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    $testSuiteRoot = Assert-DocuVerifyChildPath -CandidatePath (Join-Path $temporaryRoot 'docuverify-tests') -BoundaryRoot $temporaryRoot -Description 'DocuVerify test-suite root'
    $runName = Get-DocuVerifyTestRunName
    $comparisonRunName = Get-DocuVerifyTestRunName
    if ($runName -eq $comparisonRunName) {
        throw 'Unique test-run path generation produced a collision.'
    }
    $testRunRoot = Assert-DocuVerifyChildPath -CandidatePath (Join-Path $testSuiteRoot $runName) -BoundaryRoot $testSuiteRoot -Description 'Unique test-run root'
    if ((Split-Path -Leaf $testRunRoot) -notmatch '^run-\d+-[0-9a-f]{32}$') {
        throw 'Unique test-run root does not match the required naming convention.'
    }
    $projectBoundary = [System.IO.Path]::GetFullPath($projectRoot).TrimEnd('\') + '\'
    if ($testRunRoot.StartsWith($projectBoundary, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Test runtime must not be created inside the production project tree.'
    }

    $pytestTempDirectory = Assert-DocuVerifyChildPath -CandidatePath (Join-Path $testRunRoot 'pytest') -BoundaryRoot $testRunRoot -Description 'Pytest base temporary directory'
    $pytestCacheDirectory = Assert-DocuVerifyChildPath -CandidatePath (Join-Path $pytestTempDirectory 'cache') -BoundaryRoot $pytestTempDirectory -Description 'Pytest cache directory'
    $applicationTestRuntime = Assert-DocuVerifyChildPath -CandidatePath (Join-Path $testRunRoot 'runtime') -BoundaryRoot $testRunRoot -Description 'Application test runtime directory'
    $productionRuntime = [System.IO.Path]::GetFullPath((Join-Path $backendDirectory 'runtime'))
    if ($applicationTestRuntime.Equals($productionRuntime, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Application test runtime must differ from the production runtime.'
    }

    [System.IO.Directory]::CreateDirectory($pytestCacheDirectory) | Out-Null
    [System.IO.Directory]::CreateDirectory($applicationTestRuntime) | Out-Null
    [Environment]::SetEnvironmentVariable('DOCUVERIFY_RUNTIME_DIR', $applicationTestRuntime, 'Process')
    Write-Host "Test runtime root: $testRunRoot" -ForegroundColor DarkCyan
    Write-Host 'Test runtime isolation: unique pytest and application-runtime directories verified.' -ForegroundColor DarkCyan

    Invoke-VerificationStep -Name 'Windows bootstrap selection policy' -Action {
        & (Join-Path $PSScriptRoot 'tests\test-bootstrap-selection.ps1')
    }

    Invoke-VerificationStep -Name 'Python runtime baseline' -Action {
        if (-not (Test-PythonCandidate -FilePath $virtualEnvironmentPython)) {
            throw 'The project .venv Python is missing or unusable. Run bootstrap-windows.ps1 first.'
        }
        $venvPythonCommand = [pscustomobject]@{
            FilePath        = $virtualEnvironmentPython
            PrefixArguments = [string[]]@()
            Label           = 'project virtual environment'
        }
        $venvPythonVersion = Get-DocuVerifyPythonVersion -PythonCommand $venvPythonCommand
        $testedPythonBaseline = Get-DocuVerifyTestedPythonBaseline
        if (-not $venvPythonVersion.StartsWith($testedPythonBaseline + '.', [System.StringComparison]::Ordinal)) {
            throw "The project .venv uses Python $venvPythonVersion; the tested dependency baseline requires Python $testedPythonBaseline.x. Preserve and move the mismatched environment aside, then rerun bootstrap."
        }
        Invoke-DocuVerifyNativeCommand -FilePath $virtualEnvironmentPython -ArgumentList @(
            '-m', 'pip', 'check'
        ) -FailureMessage 'Python dependency consistency check failed'
        Write-Host "Project runtime: Python $venvPythonVersion; dependency consistency passed." -ForegroundColor Green
    }

    if (Test-Path -LiteralPath $virtualEnvironmentPython -PathType Leaf) {
        Invoke-VerificationStep -Name 'Backend tests' -Action {
            # Pytest may clear --basetemp. This invocation receives a fresh,
            # current-user-owned directory that cannot collide with another run.

            Push-Location $projectRoot
            try {
                Invoke-DocuVerifyNativeCommand -FilePath $virtualEnvironmentPython -ArgumentList @(
                    '-m', 'pytest', 'backend\tests', '--basetemp', $pytestTempDirectory,
                    '-o', "cache_dir=$pytestCacheDirectory"
                ) -FailureMessage 'Backend tests failed'
            }
            finally {
                Pop-Location
            }
        }
    }
    else {
        Write-Host 'FAIL: Backend tests - .venv is missing. Run bootstrap-windows.ps1 first.' -ForegroundColor Red
        $failedSteps.Add('Backend tests')
    }

$npmCommand = Get-Command npm.cmd -CommandType Application -ErrorAction SilentlyContinue
$frontendPackage = Join-Path $frontendDirectory 'package.json'
if ($null -eq $npmCommand -or -not (Test-Path -LiteralPath $frontendPackage -PathType Leaf)) {
    $frontendReason = $(if ($null -eq $npmCommand) { 'npm.cmd is missing' } else { 'frontend\package.json is missing' })
    foreach ($frontendStep in @('Frontend tests', 'Frontend typecheck', 'Frontend production build')) {
        Write-Host "FAIL: $frontendStep - $frontendReason." -ForegroundColor Red
        $failedSteps.Add($frontendStep)
    }
}
else {
    Invoke-VerificationStep -Name 'Frontend tests' -Action {
        Push-Location $frontendDirectory
        try {
            Invoke-DocuVerifyNativeCommand -FilePath $npmCommand.Source -ArgumentList @('test') -FailureMessage 'Frontend tests failed'
        }
        finally {
            Pop-Location
        }
    }

    Invoke-VerificationStep -Name 'Frontend typecheck' -Action {
        Push-Location $frontendDirectory
        try {
            Invoke-DocuVerifyNativeCommand -FilePath $npmCommand.Source -ArgumentList @('run', 'typecheck') -FailureMessage 'Frontend typecheck failed'
        }
        finally {
            Pop-Location
        }
    }

    Invoke-VerificationStep -Name 'Frontend production build' -Action {
        Push-Location $frontendDirectory
        try {
            Invoke-DocuVerifyNativeCommand -FilePath $npmCommand.Source -ArgumentList @('run', 'build') -FailureMessage 'Frontend production build failed'
        }
        finally {
            Pop-Location
        }
    }
}

    Write-Host ''
    if ($failedSteps.Count -gt 0) {
        Write-Host ('FAILED: ' + ($failedSteps -join ', ')) -ForegroundColor Red
        $scriptExitCode = 1
    }
    else {
        Write-Host 'PASS: all mandatory backend and frontend checks completed successfully.' -ForegroundColor Green
        $scriptExitCode = 0
    }
}
finally {
    if ($hadPreviousRuntimeOverride) {
        [Environment]::SetEnvironmentVariable('DOCUVERIFY_RUNTIME_DIR', $previousRuntimeOverride, 'Process')
    }
    else {
        [Environment]::SetEnvironmentVariable('DOCUVERIFY_RUNTIME_DIR', $null, 'Process')
    }

    if ($null -ne $testRunRoot) {
        try {
            Remove-DocuVerifyTestRun -RunRoot $testRunRoot -TemporaryRoot $temporaryRoot
            Write-Host 'PASS: unique test runtime cleanup completed.' -ForegroundColor Green
        }
        catch {
            Write-Warning "Unique test runtime cleanup was not completed: $($_.Exception.Message)"
        }
    }
}

exit $scriptExitCode
