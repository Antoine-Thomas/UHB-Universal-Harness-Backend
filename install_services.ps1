# UHB service bootstrap (Windows) — thin wrapper around `uhb.services.install`.
# The logic lives in Python, once: this script only finds an interpreter and hands over.
$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)

$python = $null
if ($env:UHB_PYTHON) {
    $python = $env:UHB_PYTHON
} else {
    foreach ($candidate in @("py", "python", "python3")) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $found) { continue }
        $probe = @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)")
        if ($candidate -eq "py") { $probe = @("-3.11") + $probe }
        & $candidate @probe 2>$null
        if ($LASTEXITCODE -eq 0) { $python = $candidate; break }
    }
}

if (-not $python) {
    Write-Error "UHB needs Python 3.11 or newer on PATH (or set UHB_PYTHON)."
    exit 1
}

$invoke = if ($python -eq "py") { @("py", "-3.11") } else { @($python) }
$exe = $invoke[0]
$prefix = @()
if ($invoke.Length -gt 1) { $prefix = $invoke[1..($invoke.Length - 1)] }

& $exe @($prefix + @("-c", "import uhb")) 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "The uhb package is not importable from here. Install it first:  python -m pip install -e ."
    exit 1
}

& $exe @($prefix + @("-m", "uhb.services.install") + $args)
exit $LASTEXITCODE
