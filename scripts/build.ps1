Param(
    [ValidateSet("all", "gui", "cli")]
    [string]$Target = "all",
    [switch]$OneFile
)

$ErrorActionPreference = "Stop"

function Invoke-Build {
    Param(
        [string]$Name,
        [switch]$Windowed
    )

    $args = @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name", $Name,
        "--hidden-import", "vc",
        "--hidden-import", "vc.__main__",
        "--hidden-import", "dashscope",
        "--hidden-import", "dashscope.audio.asr",
        "--collect-submodules", "PySide6",
        "--add-data", "config.example.yaml;."
    )

    if ($OneFile) {
        $args += "--onefile"
    } else {
        $args += "--onedir"
    }

    if ($Windowed) {
        $args += "--windowed"
    } else {
        $args += "--console"
    }

    $args += "main.py"

    Write-Host "Building $Name ..."
    python @args
}

if (-not (Test-Path "main.py")) {
    throw "Please run this script from repository root."
}

if ($Target -eq "all" -or $Target -eq "gui") {
    Invoke-Build -Name "voice2control" -Windowed
}

if ($Target -eq "all" -or $Target -eq "cli") {
    Invoke-Build -Name "voice2control-cli"
}

Write-Host ""
Write-Host "Build complete."
Write-Host "Artifacts:"
Write-Host "  dist/voice2control"
Write-Host "  dist/voice2control-cli"
