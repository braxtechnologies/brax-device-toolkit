param([string]$Python = 'python', [string]$Output = "$PSScriptRoot\dist")
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force $Output | Out-Null
$Output = (Resolve-Path -LiteralPath $Output).Path
& $Python "$PSScriptRoot\tools\collect_licenses.py" "$Output\licenses"
if ($LASTEXITCODE -ne 0) { throw 'Dependency license collection failed.' }
# Both distributions include Python. Vendor flashing software and USB drivers
# remain external dependencies; never copy a workstation's vendor folder here.
$common = @('--noconfirm', '--windowed', '--name', 'BraxRepairTool',
    '--paths', $PSScriptRoot,
    '--add-data', "$PSScriptRoot\brax_snwriter\assets;brax_snwriter/assets",
    '--add-data', "$PSScriptRoot\capabilities.json;.",
    '--add-data', "$Output\licenses;licenses")
& $Python -m PyInstaller @common --onedir `
    --distpath $Output --workpath "$Output\build" --specpath $Output `
    "$PSScriptRoot\repair_app.py"
if ($LASTEXITCODE -ne 0) { throw 'Executable build failed.' }
& $Python -m PyInstaller @common --onefile `
    --distpath "$Output\standalone" --workpath "$Output\build-onefile" --specpath "$Output\standalone" `
    "$PSScriptRoot\repair_app.py"
if ($LASTEXITCODE -ne 0) { throw 'Standalone executable build failed.' }
$release = Join-Path $Output 'release'
New-Item -ItemType Directory -Force $release | Out-Null
Copy-Item -LiteralPath "$Output\standalone\BraxRepairTool.exe" -Destination "$release\BraxDeviceToolkit-Windows.exe" -Force
foreach ($file in @('README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'WINDOWS-REPAIR-APP.md')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $file) -Destination "$Output\BraxRepairTool" -Force
}
Compress-Archive -Path "$Output\BraxRepairTool" -DestinationPath "$release\BraxDeviceToolkit-Windows.zip" -Force
$lines = Get-ChildItem -LiteralPath $release -File | Where-Object { $_.Extension -in @('.exe', '.zip') } | Sort-Object Name | ForEach-Object {
    '{0}  {1}' -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(), $_.Name
}
[IO.File]::WriteAllLines("$release\SHA256SUMS.txt", [string[]]$lines, [Text.UTF8Encoding]::new($false))
