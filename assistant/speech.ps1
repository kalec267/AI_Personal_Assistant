param([ValidateSet('list', 'speak')][string]$Action)
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Speech
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    if ($Action -eq 'list') {
        $voices = @($speaker.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object {
            @{ name = $_.VoiceInfo.Name; culture = $_.VoiceInfo.Culture.Name; gender = $_.VoiceInfo.Gender.ToString() }
        })
        ConvertTo-Json -InputObject $voices -Compress
    } else {
        $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
        $speaker.SelectVoice([string]$request.voice)
        $speaker.Speak([string]$request.text)
    }
} finally {
    $speaker.Dispose()
}
