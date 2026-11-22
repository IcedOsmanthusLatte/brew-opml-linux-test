$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$runtime = Join-Path $env:RUNNER_TEMP 'brew-opml-windows'
$proof = Join-Path $env:GITHUB_WORKSPACE 'windows-opml-evidence'
New-Item -ItemType Directory -Path $runtime,$proof -Force | Out-Null
$packages = @(
    @{
        app = 'feedflow'
        version = '1.21.0'
        file = 'FeedFlow-1.21.0.msi'
        repo = 'prof18/feed-flow'
        tag = '1.21.0-all'
        sha = '35926189386c98ad3bc5af24c1dceb9e08b7a1f24db797874aee66579c3c93e8'
    }
    @{
        app = 'fluent'
        version = '1.3.2'
        file = 'Fluent.Reader.Unpacked.1.3.2.x64.zip'
        repo = 'yang991178/fluent-reader'
        tag = 'v1.3.2'
        sha = '2db9c743903b4a8028495373b39f1cf01999add38a352bc394aaa0400d1f231e'
    }
)
$fixture = Join-Path $PSScriptRoot 'control.opml'
$fixtureSha = (Get-FileHash -LiteralPath $fixture -Algorithm SHA256).Hash.ToLowerInvariant()
if ($fixtureSha -ne '822295536b4776a25750d7dc4b707afff1fe9086d33382e6148b984195ed1afd') {
    throw "Unexpected fixture SHA256 $fixtureSha"
}
$os = Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,BuildNumber,OSArchitecture
$proofPackage = @{ fixture_sha256 = $fixtureSha; os = $os; runner_image = $env:ImageVersion; packages = @() }
$proofFile = Join-Path $proof 'environment.json'
$proofPackage | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $proofFile -Encoding utf8NoBOM
$jobs = foreach ($pkg in $packages) {
    $url = "https://github.com/$($pkg.repo)/releases/download/$($pkg.tag)/$($pkg.file)"
    $out = Join-Path $runtime $pkg.file
    Start-Job -Name $pkg.app -ArgumentList @($url,$out) -ScriptBlock {
        param($url,$out)
        curl.exe --fail --retry 2 --silent --show-error --location $url --output $out
        if ($LASTEXITCODE -ne 0) { throw 'Official release download failed.' }
    }
}
$jobs | Wait-Job -Timeout 160 | Out-Null
foreach ($job in $jobs) {
    Receive-Job -Job $job -ErrorAction Stop
    if ($job.State -ne 'Completed') { throw "Download job $($job.Name) did not complete." }
}
foreach ($pkg in $packages) {
    $path = Join-Path $runtime $pkg.file
    $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $pkg.sha) { throw "Unexpected hash for $($pkg.file): $actual" }
    $proofPackage.packages += @{ app = $pkg.app; version = $pkg.version; binary = $pkg.file; sha256 = $actual }
}
$proofPackage | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $proofFile -Encoding utf8NoBOM
$msi = Join-Path $runtime $packages[0].file
$installer = Start-Process msiexec.exe -ArgumentList "/i `"$msi`" /quiet /qn /norestart" -Wait -PassThru -WindowStyle Hidden
if ($installer.ExitCode -notin @(0,3010)) { throw "FeedFlow MSI exit $($installer.ExitCode)." }
$feedRoots = @(
    (Join-Path $env:ProgramFiles 'FeedFlow'),
    (Join-Path $env:LOCALAPPDATA 'FeedFlow'),
    (Join-Path $env:LOCALAPPDATA 'Programs/FeedFlow')
)
$feedExec = foreach ($root in $feedRoots) {
    if (Test-Path -LiteralPath $root) {
        Get-ChildItem -LiteralPath $root -Recurse -Filter FeedFlow.exe -File
    }
}
$feedExec = $feedExec | Select-Object -First 1
if (-not $feedExec) { throw 'FeedFlow.exe was missing after successful installer exit.' }
$unpacked = Join-Path $runtime 'fluent-program'
Expand-Archive -LiteralPath (Join-Path $runtime $packages[1].file) -DestinationPath $unpacked -Force
$fluentExec = Get-ChildItem -LiteralPath $unpacked -Recurse -Filter 'Fluent Reader.exe' -File | Select-Object -First 1
if (-not $fluentExec) { throw 'Fluent Reader.exe was missing from official zip.' }
@(
    "BREW_WIN_RUNTIME=$runtime",
    "BREW_WIN_FEEDFLOW=$($feedExec.FullName)",
    "BREW_WIN_FLUENT=$($fluentExec.FullName)"
) | Add-Content -LiteralPath $env:GITHUB_ENV
