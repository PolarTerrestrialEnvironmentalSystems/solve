param(
    [string]$OutputDirectory = 'C:\Users\jowals001\awi\solve\dummy_data\fgg_data',
    [string]$PythonExecutable = 'C:\Users\jowals001\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe',
    [switch]$AcceptTerms
)

$ErrorActionPreference = 'Stop'
if (-not $AcceptTerms) {
    throw 'Der Start erfordert -AcceptTerms. Bedingungen: https://www.elbe-datenportal.de/FisFggElbe/content/statisch/nutzungsbedingungen.jsp'
}
$DownloaderScript = Join-Path $PSScriptRoot 'downloader.py'
if (-not (Test-Path -LiteralPath $DownloaderScript -PathType Leaf)) {
    throw 'downloader.py fehlt.'
}
if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw 'Python fehlt. Bitte -PythonExecutable mit dem vollständigen Python-Pfad angeben.'
}
$DownloaderStateDirectory = Join-Path $OutputDirectory '_state'
New-Item -ItemType Directory -Path $DownloaderStateDirectory -Force | Out-Null
$DownloaderTimestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$DownloaderStdout = Join-Path $DownloaderStateDirectory ("stdout_" + $DownloaderTimestamp + '.log')
$DownloaderStderr = Join-Path $DownloaderStateDirectory ("stderr_" + $DownloaderTimestamp + '.log')
$DownloaderArguments = @('-X', 'utf8', '-u', ('"' + $DownloaderScript + '"'), 'resume', '--accept-terms',
                         '--output', ('"' + $OutputDirectory + '"'))
$DownloaderStartParameters = @{
    FilePath = $PythonExecutable
    ArgumentList = $DownloaderArguments
    WorkingDirectory = $PSScriptRoot
    WindowStyle = 'Hidden'
    RedirectStandardOutput = $DownloaderStdout
    RedirectStandardError = $DownloaderStderr
    PassThru = $true
}
$DownloaderProcess = Start-Process @DownloaderStartParameters
[pscustomobject]@{
    ProcessId = $DownloaderProcess.Id
    OutputDirectory = $OutputDirectory
    StandardOutput = $DownloaderStdout
    StandardError = $DownloaderStderr
}
