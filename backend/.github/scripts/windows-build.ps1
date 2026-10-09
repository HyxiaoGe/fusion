param(
    [Parameter(Mandatory = $true)]
    [string]$ImageName,

    [Parameter(Mandatory = $true)]
    [string]$AdapterImageName,

    [Parameter(Mandatory = $true)]
    [string]$ImageTag
)

# 部署只构建镜像：同一提交的检查和测试由 Fusion CI（push master）跑，见 linux-build-and-test.sh。
$ErrorActionPreference = "Stop"
$image = "${ImageName}:${ImageTag}"
$adapterImage = "${AdapterImageName}:${ImageTag}"
$appRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))

docker build --target production --provenance=false -t $image $appRoot
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

docker build --target production --provenance=false -t $adapterImage (Join-Path $appRoot "flyai-adapter")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
