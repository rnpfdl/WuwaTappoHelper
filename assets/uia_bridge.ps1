# UI Automation bridge for WuwaAutoChest.
# Outputs one compact JSON object per line.  Runs without a visible console.
$ErrorActionPreference = "SilentlyContinue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$root = [System.Windows.Automation.AutomationElement]::RootElement

function Get-WuwaWindow {
    # Avoid Get-Process / WMI: a sandboxed child may not be allowed to enumerate
    # processes, while UI Automation can still see the top-level windows.
    $children = $root.FindAll(
        [System.Windows.Automation.TreeScope]::Children,
        [System.Windows.Automation.Condition]::TrueCondition
    )
    $best = $null
    $bestScore = -1
    $debug = @()
    for ($i = 0; $i -lt $children.Count; $i++) {
        $w = $children.Item($i)
        if ([string]$w.Current.Name -ne "Wuwa Tappo") { continue }
        if ([string]$w.Current.ClassName -ne "Tauri Window") { continue }
        $r = $w.Current.BoundingRectangle
        $area = [double]$r.Width * [double]$r.Height
        try {
            $cnt = $w.FindAll(
                [System.Windows.Automation.TreeScope]::Descendants,
                [System.Windows.Automation.Condition]::TrueCondition
            ).Count
        } catch { $cnt = 0 }
        $debug += [pscustomobject]@{ area = [int]$area; count = [int]$cnt }
        $score = [int]$cnt * 1000000000 + [int]$area
        if ($score -gt $bestScore) {
            $bestScore = $score
            $best = $w
        }
    }
    $script:lastCandidates = $debug
    return $best
}

while ($true) {
    $win = Get-WuwaWindow
    if (-not $win) {
        Write-Output '{"ok":false,"reason":"未找到 Wuwa Tappo"}'
        [Console]::Out.Flush()
        Start-Sleep -Milliseconds 800
        continue
    }

    $total = $null
    $slots = @()
    $reward = $null
    try {
        $all = $win.FindAll(
            [System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition
        )
        for ($i = 0; $i -lt $all.Count; $i++) {
            $e = $all.Item($i)
            $cls = [string]$e.Current.ClassName
            $name = [string]$e.Current.Name
            $low = $cls.ToLower()
            if ($low -match 'chest-(drop-motion|reward-popup)') {
                if ($low -match 'chest-reward-popup--(green|blue|purple|gold)') {
                    $reward = $Matches[1]
                } elseif ($low -match 'level-([1-4])') {
                    $map = @{ '1' = 'green'; '2' = 'blue'; '3' = 'purple'; '4' = 'gold' }
                    $reward = $map[$Matches[1]]
                }
            }
            if ($name -match '^\d[\d,]*$') {
                $v = [int]($name -replace ',', '')
                if ($v -ge 0 -and $v -lt 1000000000) {
                    if ($null -eq $total -or $v -gt $total) { $total = $v }
                }
            }
            if ($cls -eq 'reward-countdown' -or $cls -eq 'reward-btn') {
                $rect = $e.Current.BoundingRectangle
                $ready = ($cls -eq 'reward-btn' -and $e.Current.IsEnabled)
                $slots += [pscustomobject]@{ x = [int]$rect.Left; ready = [bool]$ready }
            }
        }
    } catch { }

    $slots = @($slots | Sort-Object x)
    $leftReady = $false
    $rightReady = $false
    if ($slots.Count -ge 1) { $leftReady = [bool]$slots[0].ready }
    if ($slots.Count -ge 2) { $rightReady = [bool]$slots[1].ready }

    $obj = [ordered]@{
        ok    = $true
        total = $total
        left  = @{ ready = $leftReady }
        right = @{ ready = $rightReady }
        reward = $reward
        debug = $script:lastCandidates
    }
    $json = $obj | ConvertTo-Json -Compress -Depth 4
    Write-Output $json
    [Console]::Out.Flush()
    Start-Sleep -Milliseconds 350
}
