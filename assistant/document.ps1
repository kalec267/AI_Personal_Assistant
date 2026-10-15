$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$inputData = [Console]::In.ReadToEnd() | ConvertFrom-Json
$office = $null
$presentation = $null
try {
    $office = New-Object -ComObject PowerPoint.Application
    $oldSecurity = $office.AutomationSecurity
    $oldAlerts = $office.DisplayAlerts
    $office.AutomationSecurity = 3 # Force-disable document macros.
    $office.DisplayAlerts = 1 # ppAlertsNone
    foreach ($openPresentation in $office.Presentations) {
        if ($openPresentation.FullName -eq [string]$inputData.path) { throw 'Close the presentation before attaching it, or save a .pptx copy.' }
    }
    $presentation = $office.Presentations.Open([string]$inputData.path, -1, 0, 0)
    $text = [System.Text.StringBuilder]::new()
    $truncated = $false
    for ($index = 1; $index -le $presentation.Slides.Count; $index++) {
        if ($index -gt 200 -or $text.Length -ge 40000) { $truncated = $true; break }
        [void]$text.AppendLine("[Slide $index]")
        foreach ($shape in $presentation.Slides.Item($index).Shapes) {
            if ($shape.HasTextFrame -and $shape.TextFrame.HasText) {
                [void]$text.AppendLine($shape.TextFrame.TextRange.Text)
            }
            if ($shape.HasTable) {
                foreach ($row in $shape.Table.Rows) {
                    $cells = foreach ($cell in $row.Cells) { $cell.Shape.TextFrame.TextRange.Text }
                    [void]$text.AppendLine(($cells -join ' | '))
                }
            }
            if ($text.Length -ge 40000) { $truncated = $true; break }
        }
    }
    @{ text = $text.ToString().Substring(0, [Math]::Min(40000, $text.Length)); truncated = $truncated } | ConvertTo-Json -Compress
} finally {
    if ($null -ne $presentation) { $presentation.Close(); [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($presentation) }
    if ($null -ne $office) {
        if ($office.Presentations.Count -eq 0) { $office.Quit() }
        else { $office.AutomationSecurity = $oldSecurity; $office.DisplayAlerts = $oldAlerts }
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($office)
    }
}
