param(
    [string]$AdminUsername = "azureuser"
)

$ErrorActionPreference = "Stop"

$stateDir = "C:\ProgramData\AzureVmCreator"
$installerPath = Join-Path $stateDir "configure_windows_vm.ps1"
$logPath = Join-Path $stateDir "configure.log"
$taskName = "AzureVmCreator-ConfigureDesktop"

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Output ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message)
}

function Get-TaskUser {
    param([Parameter(Mandatory = $true)][string]$Username)

    if ($Username -match "\\|@") {
        return $Username
    }

    return ("{0}\{1}" -f $env:COMPUTERNAME, $Username)
}

Write-Step "Starting Azure VM Creator staging."
Write-Step "Ensuring state directory exists: $stateDir"
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null

$installerScript = @'
$ErrorActionPreference = "Stop"

$stateDir = "C:\ProgramData\AzureVmCreator"
$markerPath = Join-Path $stateDir "configured.txt"
$logPath = Join-Path $stateDir "configure.log"
$taskName = "AzureVmCreator-ConfigureDesktop"
$wingetCommand = $null

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message)
}

function Resolve-WingetCommand {
    $command = Get-Command winget -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $desktopAppInstaller = Get-AppxPackage -Name Microsoft.DesktopAppInstaller -ErrorAction SilentlyContinue |
        Sort-Object -Property Version -Descending |
        Select-Object -First 1
    if ($desktopAppInstaller -and $desktopAppInstaller.InstallLocation) {
        $packagedWinget = Join-Path $desktopAppInstaller.InstallLocation "winget.exe"
        if (Test-Path -LiteralPath $packagedWinget) {
            return $packagedWinget
        }
    }

    $candidatePaths = @(
        "$env:LOCALAPPDATA\Microsoft\WindowsApps\winget.exe"
    )

    foreach ($path in $candidatePaths) {
        if (Test-Path -LiteralPath $path) {
            return $path
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

    Write-Step ("Running: {0} {1}" -f $Command, ($Arguments -join " "))
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command failed with exit code $LASTEXITCODE"
    }
    Write-Step ("Completed: {0} {1}" -f $Command, ($Arguments -join " "))
}

function Invoke-OptionalLoggedCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    Write-Step ("Running optional diagnostic: {0} {1}" -f $Command, ($Arguments -join " "))
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        Write-Step ("Optional diagnostic failed with exit code {0}; continuing." -f $LASTEXITCODE)
        return
    }
    Write-Step ("Completed optional diagnostic: {0} {1}" -f $Command, ($Arguments -join " "))
}

# Returns a single boolean and writes NOTHING to the success stream.
function Test-WingetPackageInstalled {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Id,

        [string]$Source = ""
    )

    $arguments = @("list", "--id", $Id, "--exact", "--accept-source-agreements", "--disable-interactivity")
    if ($Source) {
        $arguments += @("--source", $Source)
    }

    $output = & $script:wingetCommand @arguments 2>&1
    $exitCode = $LASTEXITCODE
    $text = ($output -join "`n")

    # winget list returns exit 0 only when a matching installed package is found.
    return ($exitCode -eq 0 -and ($text -match [regex]::Escape($Id)))
}

function Install-WingetPackage {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Id,

        [string]$Source = "",

        [switch]$Exact
    )

    Write-Step ("--- Package: {0} ---" -f $Id)
    Write-Step ("Checking installed package: {0}" -f $Id)
    if (Test-WingetPackageInstalled -Id $Id -Source $Source) {
        Write-Step "Already installed: $Id"
        Write-Step ("Finished package: {0}" -f $Id)
        return
    }
    Write-Step "Not installed; installing: $Id"

    $arguments = @("install")
    if ($Exact) {
        $arguments += @("-e", "--id", $Id)
    } else {
        $arguments += $Id
    }

    if ($Source) {
        $arguments += @("-s", $Source)
    }

    $arguments += @("--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity")
    Invoke-LoggedCommand -Command $script:wingetCommand -Arguments $arguments
    Write-Step ("Finished package: {0}" -f $Id)
}

try {
    New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
    Start-Transcript -LiteralPath $logPath -Append | Out-Null

    Write-Step "Starting Azure VM Creator desktop configuration."
    $script:wingetCommand = Resolve-WingetCommand
    if (-not $script:wingetCommand) {
        throw "winget could not be resolved in the interactive desktop session."
    }
    Write-Step "Using winget: $script:wingetCommand"

    Write-Step "Checking configured winget sources."
    Invoke-OptionalLoggedCommand -Command $script:wingetCommand -Arguments @("source", "list")

    Write-Step "Installing configured packages."
    Install-WingetPackage -Id "9PLM9XGG6VKS" -Source "msstore"
    Install-WingetPackage -Id "Anthropic.Claude" -Source "winget" -Exact
    Install-WingetPackage -Id "Git.Git" -Source "winget" -Exact

    Write-Step "Writing configuration marker."
    "Configured by azure-vm-creator at $(Get-Date -Format o)" | Set-Content -LiteralPath $markerPath -Encoding UTF8

    Write-Step "Removing scheduled task: $taskName"
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

    Write-Step "Azure VM Creator desktop configuration finished successfully."
} catch {
    Write-Host ""
    Write-Host "Azure VM Creator desktop configuration failed. The scheduled task is left in place for retry."
    Write-Error $_
    exit 1
} finally {
    try {
        Stop-Transcript | Out-Null
    } catch {
    }
}

Write-Host ""
Write-Host "Configuration complete. Press Enter to close this window."
Read-Host | Out-Null
'@

Write-Step "Writing installer script: $installerPath"
$installerScript | Set-Content -LiteralPath $installerPath -Encoding UTF8

$taskUser = Get-TaskUser -Username $AdminUsername
Write-Step "Registering scheduled task $taskName for interactive logon user $taskUser"
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument ('-NoExit -ExecutionPolicy Bypass -File "{0}"' -f $installerPath)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $taskUser
$principal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Azure VM Creator one-time desktop app configuration." `
    -Force | Out-Null

Write-Step "Scheduled task staged."
Write-Step "Log in through Bastion as $AdminUsername. A visible PowerShell window will run the desktop installer."
Write-Step "Installer log path: $logPath"
