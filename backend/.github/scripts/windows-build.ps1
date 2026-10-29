param(
    [Parameter(Mandatory = $true)]
    [string]$ImageName,

    [Parameter(Mandatory = $true)]
    [string]$ImageTag
)

# Release only builds images; Fusion CI runs checks and tests for the same commit (linux-build-and-test.sh).
$ErrorActionPreference = "Stop"
$image = "${ImageName}:${ImageTag}"
$appRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))

# 拉 Docker Hub 基础镜像/语法前端偶发 EOF，与 UI 构建（windows-docker-build action）同样重试 3 次。
function Invoke-DockerBuildWithRetry {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Label,

        [Parameter(Mandatory = $true)]
        [scriptblock]$Build
    )

    for ($attempt = 1; $attempt -le 3; $attempt++) {
        & $Build
        $buildExitCode = $LASTEXITCODE
        if ($buildExitCode -eq 0) { return }
        if ($attempt -eq 3) { exit $buildExitCode }

        $delay = 20 * $attempt
        Write-Host "$Label docker build 失败，第 $attempt 次，${delay}s 后重试..."
        Start-Sleep -Seconds $delay
    }
}

Invoke-DockerBuildWithRetry -Label "API" -Build {
    docker build --target production --provenance=false -t $image $appRoot
}
