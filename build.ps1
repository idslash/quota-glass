$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $projectRoot
try {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path $vswhere)) {
        throw "Visual Studio Build Tools with the Desktop development with C++ workload is required."
    }
    $msbuild = & $vswhere -latest -prerelease -products * -requires Microsoft.Component.MSBuild -find "MSBuild\**\Bin\MSBuild.exe" | Select-Object -First 1
    if (-not $msbuild) {
        throw "MSBuild was not found. Install Visual Studio Build Tools."
    }

    python -m pip install -r requirements-dev.txt
    python -m pytest
    & $msbuild native\LimitBar.DX11\LimitBar.DX11.vcxproj /t:Rebuild /p:Configuration=Release /p:Platform=x64
    python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name LimitBar `
        --icon assets\limitbar.ico `
        --paths src `
        src\limitbar\__main__.py

    $glassDir = Join-Path $projectRoot "dist\glass"
    New-Item -ItemType Directory -Force -Path $glassDir | Out-Null
    Copy-Item "native\LimitBar.DX11\bin\LimitBar.Glass.exe" $glassDir -Force
    Copy-Item "native\LimitBar.DX11\THIRD-PARTY-LICENSE-liquidDX11.txt" $glassDir -Force
    Copy-Item "native\LimitBar.DX11\THIRD-PARTY-NOTICES.md" $glassDir -Force
    Write-Host "Built: $projectRoot\dist\LimitBar.exe + glass\LimitBar.Glass.exe"
}
finally {
    Pop-Location
}
