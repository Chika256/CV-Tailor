$ErrorActionPreference = 'Stop'

$shortcutPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'CV Tailor Companion.lnk'
if (Test-Path -LiteralPath $shortcutPath) {
    Remove-Item -LiteralPath $shortcutPath -Force
    Write-Output "Removed startup shortcut: $shortcutPath"
} else {
    Write-Output 'The CV Tailor startup shortcut is not installed.'
}
