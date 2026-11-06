param([Parameter(Mandatory=$true)][string]$PrivateRoot)
$root = [System.IO.Path]::GetFullPath($PrivateRoot)
if ($env:BDF_REQUIRE_E_STORAGE -eq '1' -and [System.IO.Path]::GetPathRoot($root) -ne 'E:\') {
    throw 'Student-local E-drive policy is enabled; choose E or use your own independent environment.'
}
$cache = Join-Path $root '.runtime-cache'
$names = @('TEMP','TMP','TMPDIR','MPLCONFIGDIR','IPYTHONDIR','JUPYTER_RUNTIME_DIR','JUPYTER_CONFIG_DIR','JUPYTER_DATA_DIR','PIP_CACHE_DIR','PYTHONPYCACHEPREFIX','XDG_CACHE_HOME')
foreach ($name in $names) {
    $path = Join-Path $cache $name.ToLowerInvariant()
    New-Item -ItemType Directory -Path $path -Force | Out-Null
    [Environment]::SetEnvironmentVariable($name, $path, 'Process')
}
# Process-local task storage only; this does not control Windows/Codex storage.
$env:PYTHONDONTWRITEBYTECODE = '1'
