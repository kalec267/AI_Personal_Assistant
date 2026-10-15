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
        $speed = 1.0
        if ($null -ne $request.speed) { $speed = [double]$request.speed }
        if ([double]::IsNaN($speed) -or [double]::IsInfinity($speed) -or $speed -lt 0.5 -or $speed -gt 2.0) {
            throw 'Speech speed must be between 0.5 and 2.0.'
        }
        # System.Speech uses discrete -10..10 rate steps rather than a factor.
        $speaker.Rate = [int][Math]::Round(10 * [Math]::Log($speed, 2))
        $speaker.Speak([string]$request.text)
    }
} finally {
    $speaker.Dispose()
}
