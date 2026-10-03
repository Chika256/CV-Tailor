Option Explicit
' Starts the CV Tailor companion with no console window.
' Usage: wscript start-companion-hidden.vbs "C:\path\to\workspace"
Dim shell, workspace
Set shell = CreateObject("WScript.Shell")
If WScript.Arguments.Count < 1 Then
  WScript.Quit 1
End If
workspace = WScript.Arguments(0)
shell.CurrentDirectory = workspace
shell.Run "cmd.exe /c cv-tailor serve --workspace """ & workspace & """", 0, False
