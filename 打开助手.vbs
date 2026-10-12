Option Explicit
Dim fs, shell, root, cfg, line, runtimeHome, pythonw, command
Set fs = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fs.GetParentFolderName(WScript.ScriptFullName)
pythonw = fs.BuildPath(root, "runtime\pythonw.exe")
If Not fs.FileExists(pythonw) Then
If Not fs.FileExists(fs.BuildPath(root, ".venv\pyvenv.cfg")) Then
    MsgBox "Runtime missing. Please extract the complete portable ZIP first.", 16, "HKA"
    WScript.Quit 1
End If
Set cfg = fs.OpenTextFile(fs.BuildPath(root, ".venv\pyvenv.cfg"), 1)
runtimeHome = ""
Do Until cfg.AtEndOfStream
    line = cfg.ReadLine
    If InStr(line, "=") > 0 Then
        If LCase(Trim(Split(line, "=")(0))) = "home" Then
            runtimeHome = Trim(Mid(line, InStr(line, "=") + 1))
        End If
    End If
Loop
cfg.Close
pythonw = fs.BuildPath(runtimeHome, "pythonw.exe")
End If
If Not fs.FileExists(pythonw) Then
    MsgBox "Python runtime missing. Please reinstall the HKA environment.", 16, "HKA"
    WScript.Quit 1
End If
command = Chr(34) & pythonw & Chr(34) & " " & Chr(34) & fs.BuildPath(root, "start_hka.pyw") & Chr(34)
If WScript.Arguments.Count > 0 Then
    If WScript.Arguments(0) = "--no-browser" Then command = command & " --no-browser"
End If
shell.CurrentDirectory = root
shell.Run command, 0, False
