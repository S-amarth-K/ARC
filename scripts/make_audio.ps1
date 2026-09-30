# Pre-records the fixed message library (tiers 4-5) as WAV clips using the
# Windows built-in speech engine, so the cab never depends on live TTS.
#   powershell -ExecutionPolicy Bypass -File scripts/make_audio.ps1
# Re-run after editing backend/messages.py (then: python -m backend.export_audio_lines).
Add-Type -AssemblyName System.Speech
$root = Split-Path -Parent $PSScriptRoot
$lines = Get-Content "$root\scripts\audio_lines.json" -Raw | ConvertFrom-Json
$out = "$root\frontend\public\audio"
New-Item -ItemType Directory -Force $out | Out-Null
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.Rate = 0
foreach ($p in $lines.PSObject.Properties) {
    $text = $p.Value -creplace "STOP\.", "Stop!"
    $synth.SetOutputToWaveFile("$out\$($p.Name).wav")
    $synth.Speak($text)
    Write-Output "  $($p.Name).wav  <- $text"
}
$synth.SetOutputToNull()
$synth.Dispose()

