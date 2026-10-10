<#
.SYNOPSIS
  体調分析ダッシュボードの配布用zip（Python同梱）を作る。Windowsで実行する。

.DESCRIPTION
  1. python.org の embeddable 版 Python をダウンロードして展開する。
  2. requirements.txt の依存ライブラリを、その Python 向け（Windows / 64bit）の
     ビルド済みパッケージで取得して同梱する（この作業用PCのPythonのバージョンは問わない）。
  3. アプリ本体（app.py・src・起動用の .bat など）を重ねる。
  4. 個人データが混ざっていないことを確認し、同梱の Python でライブラリを読み込めることを確認する。
  5. zip にまとめる。

  zip には、利用者のデータ・weather_config.json・.git・テスト・docs を含めない。
  データと設定は、アプリの初回起動時に %LOCALAPPDATA%\体調分析ダッシュボード へ作られる。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
#>
param(
    [string]$PythonVersion = "3.12.10",
    [string]$OutDir = "",
    [string]$WorkDir = (Join-Path $env:TEMP "health_dashboard_build"),
    [string]$PackageName = "体調分析ダッシュボード"
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).ProviderPath
if (-not $OutDir) { $OutDir = Join-Path $repo "dist" }

function Step($message) { Write-Host ""; Write-Host "== $message" -ForegroundColor Cyan }

# ---- バージョン ----
$pyproject = Get-Content (Join-Path $repo "pyproject.toml") -Encoding UTF8 -Raw
if ($pyproject -notmatch 'version\s*=\s*"([^"]+)"') { throw "pyproject.toml にバージョンがありません" }
$appVersion = $Matches[1]
$pyTag = ($PythonVersion -split "\.")[0..1] -join ""          # 3.12.10 -> 312
$pyMinor = ($PythonVersion -split "\.")[0..1] -join "."       # 3.12.10 -> 3.12
$buildDate = Get-Date -Format "yyyy-MM-dd HH:mm"

# ---- 作業フォルダ ----
Step "作業フォルダを準備する: $WorkDir"
if (Test-Path -LiteralPath $WorkDir) { Remove-Item -LiteralPath $WorkDir -Recurse -Force }
New-Item -ItemType Directory -Path $WorkDir | Out-Null
$stage = Join-Path $WorkDir $PackageName
$pyDir = Join-Path $stage "python"
New-Item -ItemType Directory -Path $pyDir | Out-Null

# ---- 1. 同梱するPython ----
Step "Python $PythonVersion（embeddable版）をダウンロードして展開する"
$embedZip = Join-Path $WorkDir "python-embed.zip"
Invoke-WebRequest -Uri "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" `
    -OutFile $embedZip -UseBasicParsing
Expand-Archive -LiteralPath $embedZip -DestinationPath $pyDir -Force

# site-packages を検索パスに加える（embeddable版は初期状態では使えない）
$pth = Join-Path $pyDir "python$pyTag._pth"
if (-not (Test-Path -LiteralPath $pth)) { throw "$pth が見つかりません" }
# "import site" は書かない。書くと、そのPCのユーザー用ライブラリ（AppData\Roaming\Python など）が
# 検索パスに混ざり、PCごとに動きが変わる／同梱し忘れたライブラリがPC側のもので補われてしまう。
@("python$pyTag.zip", ".", "Lib\site-packages") | Set-Content -LiteralPath $pth -Encoding ASCII
New-Item -ItemType Directory -Path (Join-Path $pyDir "Lib\site-packages") -Force | Out-Null

# ---- 2. 依存ライブラリ ----
Step "依存ライブラリを取得する（Windows 64bit / Python $PythonVersion 向けのビルド済みパッケージ）"
$sitePackages = Join-Path $pyDir "Lib\site-packages"
# Microsoft Store版のPythonなどは「ユーザー用にインストール」が既定で、--target と併用できない。
$env:PIP_USER = "0"
# requirements-lock.txt は日本語のコメントを含むUTF-8。日本語のWindowsの既定（cp932）で
# 読まれて失敗しないよう、PythonをUTF-8モードで動かす。
$env:PYTHONUTF8 = "1"
# テスト済みの版を固定したファイルがあれば、それを使う（無ければ、その時点の最新版）。
$lockFile = Join-Path $repo "requirements-lock.txt"
$requirements = if (Test-Path -LiteralPath $lockFile) { $lockFile } else { Join-Path $repo "requirements.txt" }
Write-Host "  使うファイル: $requirements"
& python -m pip install --disable-pip-version-check --no-warn-script-location `
    --target $sitePackages --platform win_amd64 --python-version $pyMinor `
    --implementation cp --only-binary=:all: -r $requirements
if ($LASTEXITCODE -ne 0) { throw "ライブラリの取得に失敗しました" }

# ---- 3. アプリ本体 ----
Step "アプリ本体をコピーする"
foreach ($name in @("app.py", "run_dashboard.py", "requirements.txt", "LICENSE")) {
    Copy-Item -LiteralPath (Join-Path $repo $name) -Destination $stage
}
Copy-Item -Path (Join-Path $repo "*.bat") -Destination $stage
# デスクトップのショートカットに使うアイコン（アプリが初回起動時にショートカットを作る）
Copy-Item -LiteralPath (Join-Path $repo "assets\app.ico") -Destination $stage
Copy-Item -LiteralPath (Join-Path $repo ".streamlit") -Destination $stage -Recurse
Copy-Item -LiteralPath (Join-Path $repo "src") -Destination $stage -Recurse
Copy-Item -LiteralPath (Join-Path $repo "staff_operation_guide.md") -Destination (Join-Path $stage "使い方.txt")
Get-ChildItem -LiteralPath $stage -Recurse -Directory -Filter "__pycache__" |
    Where-Object { $_.FullName -notlike "$pyDir*" } | Remove-Item -Recurse -Force

@(
    "体調・睡眠分析ダッシュボード",
    "バージョン: $appVersion",
    "作成日時: $buildDate",
    "同梱Python: $PythonVersion"
) | Set-Content -LiteralPath (Join-Path $stage "VERSION.txt") -Encoding UTF8

# ---- 4. 個人データ・設定が混ざっていないことの確認 ----
Step "個人データ・設定ファイルが混ざっていないか確認する"
$forbidden = Get-ChildItem -LiteralPath $stage -Recurse -Force |
    Where-Object {
        $_.FullName -notlike "$pyDir*" -and (
            $_.Extension -in @(".xlsx", ".xls", ".csv") -or
            $_.Name -in @("weather_config.json", ".git", "data", "logs", "uploads"))
    }
if ($forbidden) {
    $forbidden | ForEach-Object { Write-Host "  混入: $($_.FullName)" -ForegroundColor Red }
    throw "配布物に含めてはいけないファイルがあります"
}
Write-Host "  問題なし"

# ---- 5. 同梱Pythonでの動作確認 ----
Step "同梱のPythonで、ライブラリを読み込めるか確認する"
$bundledPython = Join-Path $pyDir "python.exe"
& $bundledPython -m compileall -q $sitePackages (Join-Path $stage "src") | Out-Null
$check = & $bundledPython -c "import sys, streamlit, pandas, numpy, plotly, openpyxl; sys.path.insert(0, r'$stage\src'); import health_dashboard.launcher; print('python', sys.version.split()[0], '| streamlit', streamlit.__version__, '| pandas', pandas.__version__, '| numpy', numpy.__version__)"
if ($LASTEXITCODE -ne 0) { throw "同梱のPythonでライブラリを読み込めませんでした" }
Write-Host "  $check"

Step "同梱のPythonが、同梱フォルダの外のライブラリを見ていないか確認する"
$searchPaths = & $bundledPython -c "import sys; print(chr(10).join(sys.path))"
$leaks = @($searchPaths | Where-Object { $_ -and ($_ -notlike "$pyDir*") })
if ($leaks.Count -gt 0) {
    $leaks | ForEach-Object { Write-Host "  同梱フォルダの外: $_" -ForegroundColor Red }
    throw "同梱のPythonが、このPCのライブラリを検索パスに含めています"
}
Write-Host "  問題なし（検索パスはすべて同梱フォルダの中）"

# ---- 6. zip ----
Step "zipにまとめる"
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
$zipPath = Join-Path $WorkDir "$PackageName-$appVersion.zip"
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    $stage, $zipPath, [System.IO.Compression.CompressionLevel]::Optimal, $true)
$finalZip = Join-Path $OutDir (Split-Path $zipPath -Leaf)
Copy-Item -LiteralPath $zipPath -Destination $finalZip -Force

$sizeMb = [math]::Round((Get-Item -LiteralPath $finalZip).Length / 1MB, 1)
$stageMb = [math]::Round((Get-ChildItem -LiteralPath $stage -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host ""
Write-Host "完成: $finalZip（zip $sizeMb MB / 展開後 $stageMb MB）" -ForegroundColor Green
