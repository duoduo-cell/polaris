param([string]$Dir)
# Synthesize every narration sentence to its own WAV with the built-in Zira voice (offline).
Add-Type -AssemblyName System.Speech
$scenes = Get-Content -Raw -Encoding UTF8 (Join-Path $Dir "narration.json") | ConvertFrom-Json
$outDir = Join-Path $Dir "tts"
New-Item -ItemType Directory -Force $outDir | Out-Null
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(24000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
foreach ($scene in $scenes) {
  $i = 0
  foreach ($sentence in $scene.sentences) {
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $synth.SelectVoice("Microsoft Zira Desktop")
    $synth.Rate = 1
    $path = Join-Path $outDir ("{0}_{1:D2}.wav" -f $scene.scene, $i)
    $synth.SetOutputToWaveFile($path, $format)
    $synth.Speak($sentence)
    $synth.Dispose()
    $i++
  }
}
"done"
