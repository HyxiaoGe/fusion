param(
    [string]$FixturePath = ""
)

$ErrorActionPreference = "Stop"
$expectedSha256 = "1fc5150883d4a5f85f022c9278bb779c9a2d37df1567047728c3f6f8e240fb3f"

function ConvertTo-CanonicalLf {
    param([byte[]]$Bytes)

    $result = New-Object System.Collections.Generic.List[byte]
    $index = 0
    while ($index -lt $Bytes.Length) {
        $current = $Bytes[$index]
        if ($current -eq 13) {
            if (($index + 1) -ge $Bytes.Length -or $Bytes[$index + 1] -ne 10) {
                throw "frozen Prompt fixture contains an invalid carriage return"
            }
            [void]$result.Add(10)
            $index += 2
            continue
        }
        [void]$result.Add($current)
        $index += 1
    }
    return $result.ToArray()
}

function Get-Sha256Hex {
    param([byte[]]$Bytes)

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hash = $sha256.ComputeHash($Bytes)
    } finally {
        $sha256.Dispose()
    }
    return ([System.BitConverter]::ToString($hash)).Replace("-", "").ToLowerInvariant()
}

if ([string]::IsNullOrWhiteSpace($FixturePath)) {
    $appRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
    $FixturePath = Join-Path $appRoot "test\fixtures\prompt_bundle\p3a_sync.py"
}

[byte[]]$canonical = ConvertTo-CanonicalLf ([System.IO.File]::ReadAllBytes($FixturePath))
$actualSha256 = Get-Sha256Hex $canonical
if ($actualSha256 -ne $expectedSha256) {
    throw "frozen Prompt fixture digest mismatch: expected=$expectedSha256 actual=$actualSha256"
}

$windowsBytes = New-Object System.Collections.Generic.List[byte]
foreach ($item in $canonical) {
    if ($item -eq 10) {
        [void]$windowsBytes.Add(13)
    }
    [void]$windowsBytes.Add($item)
}
[byte[]]$roundTrip = ConvertTo-CanonicalLf ($windowsBytes.ToArray())
if ($roundTrip.Length -ne $canonical.Length) {
    throw "frozen Prompt fixture CRLF round-trip length mismatch"
}
for ($index = 0; $index -lt $canonical.Length; $index += 1) {
    if ($roundTrip[$index] -ne $canonical[$index]) {
        throw "frozen Prompt fixture CRLF round-trip byte mismatch"
    }
}

$invalidRejected = $false
try {
    [void](ConvertTo-CanonicalLf ([byte[]](65, 13, 66)))
} catch {
    $invalidRejected = $true
}
if (-not $invalidRejected) {
    throw "frozen Prompt fixture invalid carriage-return control was accepted"
}

Write-Host "frozen Prompt fixture byte preflight passed: sha256=$actualSha256"
