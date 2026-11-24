$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$runtime = Join-Path $env:RUNNER_TEMP 'brew-fluent-nesting'
$evidence = Join-Path $env:GITHUB_WORKSPACE 'fluent-opml-evidence'
New-Item -ItemType Directory -Path $runtime,$evidence -Force | Out-Null

$release = 'https://github.com/yang991178/fluent-reader/releases/download/v1.3.2/Fluent.Reader.Unpacked.1.3.2.x64.zip'
$expectedSha = '2db9c743903b4a8028495373b39f1cf01999add38a352bc394aaa0400d1f231e'
$download = Join-Path $runtime 'Fluent.Reader.Unpacked.1.3.2.x64.zip'
curl.exe --fail --location --silent --show-error --retry 2 --max-time 100 --output $download $release
if ($LASTEXITCODE -ne 0) { throw 'The official Fluent Reader release download failed.' }

$actualSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $download).Hash.ToLowerInvariant()
if ($actualSha -ne $expectedSha) { throw "Official release SHA-256 mismatch: $actualSha" }

$program = Join-Path $runtime 'program'
Expand-Archive -LiteralPath $download -DestinationPath $program
$executables = @(Get-ChildItem -LiteralPath $program -Recurse -Filter 'Fluent Reader.exe' -File)
if ($executables.Count -ne 1) { throw "Expected one Fluent Reader executable, found $($executables.Count)." }
$executable = $executables[0]
$os = Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,BuildNumber,OSArchitecture

[ordered]@{
    official_release = $release
    expected_version = '1.3.2'
    executable_file_version = $executable.VersionInfo.FileVersion
    executable_product_version = $executable.VersionInfo.ProductVersion
    archive_sha256 = $actualSha
    executable_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $executable.FullName).Hash.ToLowerInvariant()
    os = $os
    runner_image = $env:ImageVersion
    workflow_run_id = $env:GITHUB_RUN_ID
    commit_sha = $env:GITHUB_SHA
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $evidence 'environment.json') -Encoding utf8NoBOM

"BREW_WIN_FLUENT=$($executable.FullName)" | Add-Content -LiteralPath $env:GITHUB_ENV
