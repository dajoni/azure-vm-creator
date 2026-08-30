$ErrorActionPreference = "Stop"

$stateDir = "C:\ProgramData\AzureVmCreator"
$markerPath = Join-Path $stateDir "configured.txt"
$wingetCommand = $null

function Resolve-WingetCommand {
    $command = Get-Command winget -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $candidatePatterns = @(
        "$env:LOCALAPPDATA\Microsoft\WindowsApps\winget.exe",
        "$env:ProgramFiles\WindowsApps\Microsoft.DesktopAppInstaller_*_x64__8wekyb3d8bbwe\winget.exe"
    )

    foreach ($pattern in $candidatePatterns) {
        $match = Resolve-Path -Path $pattern -ErrorAction SilentlyContinue |
            Sort-Object -Property Path -Descending |
            Select-Object -First 1
        if ($match) {
            return $match.Path
        }
    }

    return $null
}

function Invoke-LoggedCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    Write-Output ("Running: {0} {1}" -f $Command, ($Arguments -join " "))
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command failed with exit code $LASTEXITCODE"
    }
}

function Test-WingetPackageInstalled {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Id
    )

    $output = & $script:wingetCommand list --id $Id --exact --accept-source-agreements 2>$null
    if ($LASTEXITCODE -ne 0) {
        return $false
    }

    return (($output -join "`n") -match [regex]::Escape($Id))
}

function Install-WingetPackage {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Id,

        [string]$Source = "",

        [switch]$Exact
    )

    if (Test-WingetPackageInstalled -Id $Id) {
        Write-Output "Already installed: $Id"
        return
    }

    $arguments = @("install")
    if ($Exact) {
        $arguments += @("-e", "--id", $Id)
    } else {
        $arguments += $Id
    }

    if ($Source) {
        $arguments += @("-s", $Source)
    }

    $arguments += @("--accept-package-agreements", "--accept-source-agreements")
    Invoke-LoggedCommand -Command $script:wingetCommand -Arguments $arguments
}

$script:wingetCommand = Resolve-WingetCommand
if (-not $script:wingetCommand) {
    throw "winget could not be resolved from the Azure Run Command context. It may be installed for an interactive user but absent from this process PATH."
}
Write-Output "Using winget: $script:wingetCommand"

if (-not (Test-Path -LiteralPath $stateDir)) {
    New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
}

Install-WingetPackage -Id "9PLM9XGG6VKS" -Source "msstore"
Install-WingetPackage -Id "Anthropic.Claude" -Exact
Install-WingetPackage -Id "Git.Git" -Exact

if (-not (Test-Path -LiteralPath $markerPath)) {
    "Configured by azure-vm-creator" | Set-Content -LiteralPath $markerPath -Encoding UTF8
}

Write-Output "Azure VM Creator configuration marker written to $markerPath"
