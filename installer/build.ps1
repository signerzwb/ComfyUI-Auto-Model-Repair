param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot "..\dist")
)

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$payload = Join-Path $env:TEMP "shendumaoworkflowassistant-payload.zip"
$compiler = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$outputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
$output = Join-Path $outputDirectory "ShenDuMao-ComfyUI-Workflow-Assistant-v1.0.0-Setup.exe"

if (-not (Test-Path $compiler)) { throw "Windows .NET Framework C# compiler was not found." }
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
Remove-Item -LiteralPath $payload -Force -ErrorAction SilentlyContinue

Push-Location $projectRoot
try {
    Compress-Archive -Path @("__init__.py", "README.md", "requirements.txt", "workflow_agent", "web") -DestinationPath $payload -CompressionLevel Optimal
    & $compiler /nologo /utf8output /codepage:65001 /target:winexe /platform:anycpu /out:$output /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.IO.Compression.dll /r:System.IO.Compression.FileSystem.dll /resource:$payload,plugin_payload.zip (Join-Path $PSScriptRoot "Program.cs")
    if ($LASTEXITCODE -ne 0) { throw "Installer compilation failed." }
    $verify = Start-Process -FilePath $output -ArgumentList "--verify-payload" -PassThru -Wait
    if ($verify.ExitCode -ne 0) { throw "Installer payload verification failed." }
    Get-Item -LiteralPath $output
}
finally {
    Pop-Location
    Remove-Item -LiteralPath $payload -Force -ErrorAction SilentlyContinue
}
