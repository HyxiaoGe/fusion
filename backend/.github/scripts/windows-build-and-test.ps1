param(
    [Parameter(Mandatory = $true)]
    [string]$ImageName,

    [Parameter(Mandatory = $true)]
    [string]$AdapterImageName,

    [Parameter(Mandatory = $true)]
    [string]$ImageTag
)

$ErrorActionPreference = "Stop"
$image = "${ImageName}:${ImageTag}"
$adapterImage = "${AdapterImageName}:${ImageTag}"
$appRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$monorepoRoot = [System.IO.Path]::GetFullPath((Join-Path $appRoot ".."))
$linuxBuildScript = Join-Path $appRoot ".github\scripts\linux-build-and-test.sh"

docker build --target production --provenance=false -t $image $appRoot
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$testExitCode = 1
$normalizedLinuxScript = $null
try {
    if ([string]::IsNullOrWhiteSpace($env:RUNNER_TEMP)) {
        $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    } else {
        $tempRoot = [System.IO.Path]::GetFullPath($env:RUNNER_TEMP)
    }
    $normalizedLinuxScript = Join-Path $tempRoot ("fusion-linux-build-and-test-{0}.sh" -f [guid]::NewGuid().ToString("N"))
    $linuxBuildScriptContent = [System.IO.File]::ReadAllText($linuxBuildScript)
    $linuxBuildScriptContent = $linuxBuildScriptContent.Replace("`r`n", "`n").Replace("`r", "`n")
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($normalizedLinuxScript, $linuxBuildScriptContent, $utf8NoBom)

    docker run --rm `
        --mount "type=bind,source=$appRoot\README.md,target=/app/README.md,readonly" `
        --mount "type=bind,source=$monorepoRoot\.github,target=/.github,readonly" `
        --mount "type=bind,source=$normalizedLinuxScript,target=/app/.github/scripts/linux-build-and-test.sh,readonly" `
        $image sh -lc "timeout 300s python -m pip install --default-timeout=30 --no-cache-dir -r requirements-ci.txt && python scripts/check_architecture.py && ruff check . && timeout 270s python -u -m unittest discover -s test -t . -v && timeout 120s python -m pytest -q test/services/stream/test_run_capability_router.py test/ai/skills/test_registry.py"
    $testExitCode = $LASTEXITCODE
} finally {
    if ($null -ne $normalizedLinuxScript) {
        Remove-Item -LiteralPath $normalizedLinuxScript -Force -ErrorAction SilentlyContinue
    }
}
if ($testExitCode -ne 0) { exit $testExitCode }

docker build --target test --provenance=false -t "${adapterImage}-test" (Join-Path $appRoot "flyai-adapter")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

docker build --target production --provenance=false -t $adapterImage (Join-Path $appRoot "flyai-adapter")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
