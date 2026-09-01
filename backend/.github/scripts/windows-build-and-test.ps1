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

docker build --target production --provenance=false -t $image $appRoot
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

docker run --rm `
    --mount "type=bind,source=$appRoot\README.md,target=/app/README.md,readonly" `
    $image sh -lc "timeout 300s python -m pip install --default-timeout=30 --no-cache-dir -r requirements-ci.txt && python scripts/check_architecture.py && ruff check . && timeout 270s python -u -m unittest discover -s test -t . -v && timeout 120s python -m pytest -q test/services/stream/test_run_capability_router.py test/ai/skills/test_registry.py"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

docker build --target test --provenance=false -t "${adapterImage}-test" (Join-Path $appRoot "flyai-adapter")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

docker build --target production --provenance=false -t $adapterImage (Join-Path $appRoot "flyai-adapter")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
