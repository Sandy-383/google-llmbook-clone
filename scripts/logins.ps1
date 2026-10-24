<#
.SYNOPSIS
  Shows LLMBook sign-in activity: who is signed in now, then sign-ups, sign-ins and sign-outs
  from the API log, and keeps watching for new ones (press Ctrl+C to stop).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\logins.ps1
  powershell -ExecutionPolicy Bypass -File scripts\logins.ps1 -NoFollow   # print and exit

.NOTES
  The API log records the visitor's IP address, not the account, and starts empty whenever the
  containers are rebuilt or recreated.
#>
param([switch]$NoFollow)

$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

$meaning = @{
    "login|200"    = "signed in"
    "login|401"    = "sign-in failed (wrong password or unknown email)"
    "login|429"    = "sign-in blocked (too many attempts)"
    "login|403"    = "sign-in blocked (request from another website)"
    "logout|204"   = "signed out"
    "register|201" = "new account created"
    "register|403" = "sign-up refused (wrong or missing invite code)"
    "register|409" = "sign-up refused (email already registered)"
    "register|429" = "sign-up blocked (too many attempts)"
}

Write-Host ""
Write-Host "Signed in right now (one row per device):" -ForegroundColor Cyan
docker exec llmbook-postgres-1 psql -U llmbook -d llmbook -c "select u.email as account, to_char(s.created_at at time zone 'Asia/Kolkata', 'DD Mon HH24:MI') as signed_in, left(s.user_agent, 70) as device from auth_sessions s join users u on u.id = s.user_id where s.expires_at > now() order by s.created_at desc"

Write-Host "Sign-in activity from the API log (times in your time zone):" -ForegroundColor Cyan
$logArgs = @("compose", "logs", "api", "--no-log-prefix", "--since", "168h")
if (-not $NoFollow) { $logArgs += "--follow"; Write-Host "(watching for new activity; press Ctrl+C to stop)" }
$seen = $false
& docker @logArgs 2>$null | ForEach-Object {
    if ($_ -notmatch '"/api/auth/(login|logout|register)"') { return }
    try { $entry = $_ | ConvertFrom-Json } catch { return }
    $action = ($entry.path -split "/")[-1]
    $text = $meaning["$action|$($entry.status)"]
    if (-not $text) { $text = "$action (status $($entry.status))" }
    $time = ([datetime]$entry.timestamp).ToLocalTime().ToString("dd MMM HH:mm:ss")
    $color = if ($entry.status -ge 400) { "Yellow" } else { "Green" }
    Write-Host ("{0}   {1,-48}  from {2}" -f $time, $text, $entry.client) -ForegroundColor $color
    $seen = $true
}
if ($NoFollow -and -not $seen) {
    Write-Host "No sign-in activity in the log yet (it starts empty after the containers are rebuilt)."
}
