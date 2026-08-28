#requires -Version 5.1

Set-StrictMode -Version Latest

function Get-DocuVerifyProjectRoot {
    [CmdletBinding()]
    param()

    return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
}

function Test-PythonCandidate {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [string[]]$PrefixArguments = @()
    )

    if (-not (Test-Path -LiteralPath $FilePath -PathType Leaf)) {
        return $false
    }

    try {
        $versionOutput = & $FilePath @PrefixArguments -c 'import sys; print(sys.version_info[0], sys.version_info[1], sys.version_info[2], sep=chr(46))' 2>$null
        $candidateExitCode = $LASTEXITCODE
        return ($candidateExitCode -eq 0 -and ($versionOutput -join '') -match '^\d+\.\d+\.\d+$')
    }
    catch {
        return $false
    }
}

function Resolve-DocuVerifyPython {
    [CmdletBinding()]
    param(
        [switch]$IncludeProjectVenv
    )

    $projectRoot = Get-DocuVerifyProjectRoot
    $candidateList = New-Object 'System.Collections.Generic.List[object]'
    $candidateKeys = @{}

    function Add-PythonCandidate {
        param(
            [string]$FilePath,
            [string[]]$PrefixArguments,
            [string]$Label
        )

        if ([string]::IsNullOrWhiteSpace($FilePath)) {
            return
        }

        $candidateKey = $FilePath.ToLowerInvariant() + '|' + ($PrefixArguments -join ' ')
        if (-not $candidateKeys.ContainsKey($candidateKey)) {
            $candidateKeys[$candidateKey] = $true
            $candidateList.Add([pscustomobject]@{
                FilePath        = $FilePath
                PrefixArguments = [string[]]$PrefixArguments
                Label           = $Label
            })
        }
    }

    if ($IncludeProjectVenv) {
        $venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
        Add-PythonCandidate -FilePath $venvPython -PrefixArguments @() -Label 'project virtual environment'
    }

    $launcherCommands = @(Get-Command py.exe -CommandType Application -All -ErrorAction SilentlyContinue)
    foreach ($launcherCommand in $launcherCommands) {
        foreach ($selector in @('-3.11', '-3.12', '-3.13', '-3.10', '-3.14', '-3')) {
            Add-PythonCandidate -FilePath $launcherCommand.Source -PrefixArguments @($selector) -Label "Python launcher $selector"
        }
        Add-PythonCandidate -FilePath $launcherCommand.Source -PrefixArguments @() -Label 'Python launcher default'
    }

    foreach ($commandName in @('python.exe', 'python3.exe')) {
        $pythonCommands = @(Get-Command $commandName -CommandType Application -All -ErrorAction SilentlyContinue)
        foreach ($pythonCommand in $pythonCommands) {
            Add-PythonCandidate -FilePath $pythonCommand.Source -PrefixArguments @() -Label $commandName
        }
    }

    # Some Windows Python installations expose pip.exe while the launcher and
    # WindowsApps aliases are unusable. Infer python.exe from pip's install root
    # without hard-coding a user profile or printing the resolved personal path.
    foreach ($pipCommandName in @('pip.exe', 'pip3.exe')) {
        $pipCommands = @(Get-Command $pipCommandName -CommandType Application -All -ErrorAction SilentlyContinue)
        foreach ($pipCommand in $pipCommands) {
            $pipDirectory = Split-Path -Parent $pipCommand.Source
            $possibleInstallRoot = Split-Path -Parent $pipDirectory
            Add-PythonCandidate -FilePath (Join-Path $pipDirectory 'python.exe') -PrefixArguments @() -Label 'Python beside pip'
            Add-PythonCandidate -FilePath (Join-Path $possibleInstallRoot 'python.exe') -PrefixArguments @() -Label 'Python inferred from pip'

            # The current python.org install-manager layout places pip launchers
            # under Python\bin and interpreters under Python\pythoncore-*.
            if (Test-Path -LiteralPath $possibleInstallRoot -PathType Container) {
                $managedInstallDirectories = @(Get-ChildItem -LiteralPath $possibleInstallRoot -Directory -Filter 'pythoncore-*' -ErrorAction SilentlyContinue)
                foreach ($managedInstallDirectory in $managedInstallDirectories) {
                    Add-PythonCandidate -FilePath (Join-Path $managedInstallDirectory.FullName 'python.exe') -PrefixArguments @() -Label 'Python install-manager runtime inferred from pip'
                }
            }
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        $localPythonRoot = Join-Path $env:LOCALAPPDATA 'Python'
        if (Test-Path -LiteralPath $localPythonRoot -PathType Container) {
            $localManagedInstallDirectories = @(Get-ChildItem -LiteralPath $localPythonRoot -Directory -Filter 'pythoncore-*' -ErrorAction SilentlyContinue)
            foreach ($localManagedInstallDirectory in $localManagedInstallDirectories) {
                Add-PythonCandidate -FilePath (Join-Path $localManagedInstallDirectory.FullName 'python.exe') -PrefixArguments @() -Label 'local Python install-manager runtime'
            }
        }
    }

    foreach ($candidate in $candidateList) {
        if (Test-PythonCandidate -FilePath $candidate.FilePath -PrefixArguments $candidate.PrefixArguments) {
            return $candidate
        }
    }

    return $null
}

function Get-DocuVerifyPythonVersion {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [object]$PythonCommand
    )

    $versionText = & $PythonCommand.FilePath @($PythonCommand.PrefixArguments) -c 'import platform; print(platform.python_version())' 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw 'The selected Python interpreter stopped responding.'
    }
    return ($versionText -join '').Trim()
}

function Invoke-DocuVerifyNativeCommand {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [string[]]$ArgumentList = @(),

        [Parameter(Mandatory = $true)]
        [string]$FailureMessage
    )

    & $FilePath @ArgumentList
    $nativeExitCode = $LASTEXITCODE
    if ($nativeExitCode -ne 0) {
        throw "$FailureMessage (exit code $nativeExitCode)."
    }
}

function Import-DocuVerifyEnvironment {
    [CmdletBinding()]
    param()

    $projectRoot = Get-DocuVerifyProjectRoot
    $environmentFile = Join-Path $projectRoot '.env'
    if (-not (Test-Path -LiteralPath $environmentFile -PathType Leaf)) {
        return
    }

    foreach ($environmentLine in Get-Content -LiteralPath $environmentFile) {
        if ($environmentLine -notmatch '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') {
            continue
        }

        $environmentName = $matches[1]
        $environmentValue = $matches[2].Trim()
        if ($environmentValue.Length -ge 2 -and
            (($environmentValue.StartsWith('"') -and $environmentValue.EndsWith('"')) -or
            ($environmentValue.StartsWith("'") -and $environmentValue.EndsWith("'")))) {
            $environmentValue = $environmentValue.Substring(1, $environmentValue.Length - 2)
        }

        if ($null -eq [Environment]::GetEnvironmentVariable($environmentName, 'Process')) {
            [Environment]::SetEnvironmentVariable($environmentName, $environmentValue, 'Process')
        }
    }
}
