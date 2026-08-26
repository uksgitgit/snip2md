' Launch Snip2MD with no console window.
Option Explicit
Dim sh, fso, root, pythonw, script
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
script = root & "\snip2md.py"
pythonw = root & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pythonw) Then
  pythonw = "pythonw.exe"
End If
sh.CurrentDirectory = root
sh.Run """" & pythonw & """ """ & script & """", 0, False
