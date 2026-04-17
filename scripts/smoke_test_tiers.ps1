param(
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [string]$ApiKey = "apex-2027-vip"
)

$headers = @{
    Authorization = "Bearer $ApiKey"
    "Content-Type" = "application/json"
}

function Test-Tier {
    param([string]$Tier)

    $body = @{
        model = $Tier
        messages = @(@{ role = "user"; content = "Reply with exactly TIER_OK" })
        max_tokens = 24
        temperature = 0
    } | ConvertTo-Json -Depth 6

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $resp = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/chat/completions" -Headers $headers -Body $body -TimeoutSec 360
        $sw.Stop()
        [PSCustomObject]@{
            tier = $Tier
            ok = $true
            latency_sec = [Math]::Round($sw.Elapsed.TotalSeconds, 2)
            text = $resp.choices[0].message.content
        }
    }
    catch {
        $sw.Stop()
        [PSCustomObject]@{
            tier = $Tier
            ok = $false
            latency_sec = [Math]::Round($sw.Elapsed.TotalSeconds, 2)
            text = $_.Exception.Message
        }
    }
}

try {
    $health = Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/health" -TimeoutSec 10
    Write-Host "Health: $($health.StatusCode) $($health.Content)"
}
catch {
    Write-Host "Health check failed: $($_.Exception.Message)"
    exit 1
}

$results = @(
    Test-Tier -Tier "fast"
    Test-Tier -Tier "default"
    Test-Tier -Tier "reasoning"
)

$results | Format-Table -AutoSize

if ($results.Where({ -not $_.ok }).Count -gt 0) {
    exit 1
}
