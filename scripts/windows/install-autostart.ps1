param(
    [Parameter(Mandatory = $true)][string]$Workspace
)

# Adds a current-user startup shortcut that runs `cv-tailor serve` for the given workspace.
$ErrorActionPreference = 'Stop'

$workspacePath = (Resolve-Path -LiteralPath $Workspace).Path
if (-not (Test-Path -LiteralPath (Join-Path $workspacePath 'cv-tailor.json'))) {
    throw "No cv-tailor.json in $workspacePath. Run 'cv-tailor init' there first."
}

$startup = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startup 'CV Tailor Companion.lnk'
$launcher = Join-Path $PSScriptRoot 'start-companion-hidden.vbs'
$wscript = Join-Path $env:SystemRoot 'System32\wscript.exe'

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $wscript
$shortcut.Arguments = '"' + $launcher + '" "' + $workspacePath + '"'
$shortcut.WorkingDirectory = $workspacePath
$shortcut.Description = 'Start the local CV Tailor companion at sign-in'
$shortcut.Save()

Write-Output "Installed startup shortcut: $shortcutPath"
