#requires -Version 5.1

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path (Split-Path -Parent $PSScriptRoot) '_common.ps1')

$baseline = Get-DocuVerifyTestedPythonBaseline
if ($baseline -ne '3.12') {
    throw "Expected the tested bootstrap baseline to be Python 3.12, but found $baseline."
}

# This deterministic inventory models the portability regression exactly: both
# Python 3.11 and 3.12 are installed, with 3.11 encountered first.
$selectedVersion = Select-DocuVerifyPythonVersion -AvailableVersions @('3.11.9', '3.12.10')
if ($selectedVersion -ne '3.12') {
    throw "Expected automatic bootstrap selection to choose Python 3.12, but found $selectedVersion."
}

$explicitSelection = Select-DocuVerifyPythonVersion -AvailableVersions @('3.11.9', '3.12.10', '3.13.7') -RequestedVersion '3.13'
if ($explicitSelection -ne '3.13') {
    throw "Expected an explicit Python 3.13 request to override the automatic baseline, but found $explicitSelection."
}

$unsupportedAutomaticSelection = Select-DocuVerifyPythonVersion -AvailableVersions @('3.11.9', '3.14.0')
if ($null -ne $unsupportedAutomaticSelection) {
    throw "Automatic selection must not fall back to Python 3.11 or 3.14; found $unsupportedAutomaticSelection."
}

$launcherWithBothVersions = @(Get-Command py.exe -CommandType Application -All -ErrorAction SilentlyContinue) |
    Where-Object {
        (Test-PythonCandidate -FilePath $_.Source -PrefixArguments @('-3.11')) -and
        (Test-PythonCandidate -FilePath $_.Source -PrefixArguments @('-3.12'))
    } |
    Select-Object -First 1
if ($null -ne $launcherWithBothVersions) {
    $resolvedCommand = Resolve-DocuVerifyPython
    $resolvedVersion = Get-DocuVerifyPythonVersion -PythonCommand $resolvedCommand
    if (-not $resolvedVersion.StartsWith('3.12.', [System.StringComparison]::Ordinal)) {
        throw "With real Python 3.11 and 3.12 installations present, bootstrap resolved $resolvedVersion instead of Python 3.12."
    }
}

Write-Host 'Python selection regression passed: 3.12 wins over 3.11, and 3.14 is not selected automatically.' -ForegroundColor Green
