#Requires -Version 7.2
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
if (-not $IsWindows -or [Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne 'X64') {
    throw 'This evaluated dependency pin is for Windows x64 only.'
}
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pin = Get-Content -LiteralPath (Join-Path $projectRoot 'dependencies/cua-driver.lock.json') -Raw | ConvertFrom-Json
$runtime = Join-Path $projectRoot '.runtime/cua-driver'
$downloads = Join-Path $runtime 'downloads'
$packages = Join-Path $runtime 'packages'
[IO.Directory]::CreateDirectory($downloads) | Out-Null
[IO.Directory]::CreateDirectory($packages) | Out-Null
$archive = Join-Path $downloads $pin.asset
if (-not (Test-Path -LiteralPath $archive)) {
    $url = "https://github.com/trycua/cua/releases/download/$($pin.tag)/$($pin.asset)"
    # Write directly to the ignored cache; a partial download will fail the hash check.
    Invoke-WebRequest -Uri $url -OutFile $archive
}
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $pin.sha256) {
    throw "Archive SHA256 mismatch. Remove only '$archive' and retry; nothing has been executed."
}
$packageName = [IO.Path]::GetFileNameWithoutExtension($pin.asset)
$package = Join-Path $packages $packageName
Add-Type -AssemblyName System.IO.Compression.FileSystem
if (-not (Test-Path -LiteralPath $package)) {
    # ZipFile handles literal brackets in worktree paths (Expand-Archive does not).
    [IO.Compression.ZipFile]::ExtractToDirectory($archive, $packages)
}
# Also verify reused/existing installations against every file in the verified archive.
$zip = [IO.Compression.ZipFile]::OpenRead($archive)
try {
    foreach ($entry in $zip.Entries) {
        if (-not $entry.Name) { continue }
        $target = [IO.Path]::GetFullPath((Join-Path $packages $entry.FullName))
        if (-not $target.StartsWith($package + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Unexpected archive entry: $($entry.FullName)"
        }
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { throw "Incomplete install: $target" }
        $stream = $entry.Open()
        $hasher = [Security.Cryptography.SHA256]::Create()
        try { $expected = [Convert]::ToHexString($hasher.ComputeHash($stream)) }
        finally { $stream.Dispose(); $hasher.Dispose() }
        if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $expected) {
            throw "Installed file differs from the pinned archive: $target"
        }
    }
} finally { $zip.Dispose() }
$exe = Join-Path $package 'cua-driver.exe'
if ((Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash -ne $pin.executableSha256) {
    throw 'Installed driver executable SHA256 mismatch.'
}
Write-Output "Installed verified CUA Driver $($pin.version): $exe"
Write-Output 'No PATH, scheduled task, registry, system package, or agent configuration was changed.'
