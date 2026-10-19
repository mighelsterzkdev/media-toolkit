param([string]$Python)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

try {
    if (-not (Test-Path -LiteralPath $venvPython)) {
        $pythonCommand = $null
        $pythonArguments = @()
        $candidates = if ($Python) { @($Python) } else { @('py', 'python', 'python3') }
        foreach ($candidate in $candidates) {
            $command = Get-Command $candidate -ErrorAction SilentlyContinue
            if (-not $command) { continue }
            # Avoid the Windows Store placeholder.
            if ($command.Source -match '\\WindowsApps\\python[3]?\.exe$') { continue }
            $candidateArguments = if ($candidate -eq 'py') { @('-3') } else { @() }
            & $command.Source @candidateArguments -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>$null
            if ($LASTEXITCODE -eq 0) {
                $pythonCommand = $command.Source
                $pythonArguments = $candidateArguments
                break
            }
        }
        if (-not $pythonCommand) {
            throw 'Install Python 3.10+ from https://www.python.org/downloads/ and try again. Select Add Python to PATH.'
        }
        Write-Host 'Creating a local Python environment...'
        & $pythonCommand @pythonArguments -m venv (Join-Path $PSScriptRoot '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
    }
    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'Could not prepare pip. Check your internet connection.' }
    & $venvPython -B -m pip install --upgrade -c constraints.txt -e .
    if ($LASTEXITCODE -ne 0) { throw 'Could not install Python dependencies.' }
    & $venvPython -B -m universal_downloader.compatibility
    if ($LASTEXITCODE -ne 0) { throw 'The installed dependencies do not match this tested release.' }
    & $venvPython -B -X utf8 (Join-Path $PSScriptRoot 'scripts\install_tools.py')
    if ($LASTEXITCODE -ne 0) { throw 'Could not install the download tools.' }
    & $venvPython -B -X utf8 -m universal_downloader --doctor
    if ($LASTEXITCODE -ne 0) { throw 'Some dependencies are still missing.' }
    Write-Host "`nReady! Open start.cmd to begin."
    exit 0
} catch {
    Write-Host "`nError: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
