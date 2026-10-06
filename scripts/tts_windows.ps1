# Synthesize test dictations with the Windows (OneCore) voices - no downloads.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\tts_windows.ps1 jobs.json
#
# jobs.json: [{"voice": "Microsoft Ravi", "ssml": "<speak ...>...</speak>", "out": "x.wav"}, ...]
# Writes 16 kHz mono PCM16 WAV files (the voices' native format) to each "out" path.
# Used by scripts/stress_test.py; voices: Get-ChildItem the list printed with -List.

param(
    [Parameter(Mandatory = $true)][string]$JobFile
)
$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media.SpeechSynthesis, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType = WindowsRuntime]

$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]

function Await($operation, [Type]$resultType) {
    $task = $asTask.MakeGenericMethod($resultType).Invoke($null, @($operation))
    $null = $task.Wait(-1)
    $task.Result
}

$voices = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices
$synth = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
$jobs = Get-Content -Raw -Encoding UTF8 $JobFile | ConvertFrom-Json
$done = 0
foreach ($job in $jobs) {
    $voice = $voices | Where-Object { $_.DisplayName -eq $job.voice } | Select-Object -First 1
    if ($null -eq $voice) { throw "voice not installed: $($job.voice)" }
    $synth.Voice = $voice
    $stream = Await ($synth.SynthesizeSsmlToStreamAsync($job.ssml)) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
    $reader = New-Object Windows.Storage.Streams.DataReader($stream.GetInputStreamAt(0))
    $size = [uint32]$stream.Size
    $null = Await ($reader.LoadAsync($size)) ([uint32])
    $bytes = New-Object byte[] $size
    $reader.ReadBytes($bytes)
    $reader.Dispose()
    $stream.Dispose()
    [IO.File]::WriteAllBytes($job.out, $bytes)
    $done++
}
"synthesized $done file(s)"
