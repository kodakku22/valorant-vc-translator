# Creates a desktop shortcut to the built exe. (ASCII only: PowerShell 5.1
# misparses non-BOM UTF-8 Japanese and breaks on string literals.)
$exe = Join-Path $PSScriptRoot "dist\VCTranslator\VCTranslator.exe"
if (-not (Test-Path $exe)) { Write-Error "Build the exe first (build_exe.bat)"; exit 1 }
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "VC Translator.lnk"))
$lnk.TargetPath = $exe
$lnk.WorkingDirectory = Split-Path $exe
$lnk.IconLocation = $exe
$lnk.Description = "Valorant VC translator + English learning"
$lnk.Save()
Write-Host "Desktop shortcut created: VC Translator"
