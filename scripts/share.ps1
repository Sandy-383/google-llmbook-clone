<#
.SYNOPSIS
  Share this laptop's LLMBook with friends over the internet.

.DESCRIPTION
  Default: a permanent address with Tailscale Funnel, https://<laptop>.<tailnet>.ts.net.
  Needs Tailscale installed and signed in, with Funnel allowed for your tailnet (Tailscale
  prints a one-time approval link the first time). Funnel keeps running in the background and
  comes back after a restart once Tailscale is running, until you stop it.
  Add the address to ALLOWED_ORIGINS in .env (the script warns if it's missing).

  -QuickTunnel: a temporary Cloudflare address instead. No account needed, but the address
  changes every time it starts.

  Keep the laptop awake while sharing: when it sleeps, the address stops working.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\share.ps1               # permanent address
  powershell -ExecutionPolicy Bypass -File scripts\share.ps1 -Stop         # stop sharing
  powershell -ExecutionPolicy Bypass -File scripts\share.ps1 -QuickTunnel  # temporary address
#>
param([switch]$Stop, [switch]$QuickTunnel)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$data = Join-Path $root "data"  # git-ignored
$envFile = Join-Path $root ".env"
$pidFile = Join-Path $data "cloudflared.pid"

function Stop-QuickTunnel {
    # Stop only the tunnel this script started, never other cloudflared processes.
    if (Test-Path $pidFile) {
        $old = Get-Process -Id (Get-Content $pidFile) -ErrorAction SilentlyContinue
        if ($old -and $old.ProcessName -eq "cloudflared") { Stop-Process -Id $old.Id -Force }
        Remove-Item $pidFile
    }
}

function Get-EnvValue([string]$name) {
    if (-not (Test-Path $envFile)) { return $null }
    $line = Select-String -Path $envFile -Pattern "^$name=(.+)$" | Select-Object -First 1
    if ($line) { return $line.Matches[0].Groups[1].Value.Trim() }
    return $null
}

function Get-TailscaleStatus {
    return (& tailscale status --json 2>$null | Out-String | ConvertFrom-Json)
}

if ($Stop) {
    Stop-QuickTunnel
    if (Get-Command tailscale -ErrorAction SilentlyContinue) {
        & tailscale funnel --https=443 off 2>$null | Out-Null
    }
    Write-Host "Sharing stopped. LLMBook is still available on this laptop at http://localhost:8080"
    return
}

try {
    Invoke-RestMethod http://localhost:8080/api/healthz -TimeoutSec 5 | Out-Null
} catch {
    throw "LLMBook isn't running at http://localhost:8080. Start Docker Desktop and Ollama, then run: docker compose up -d"
}

if ($QuickTunnel) {
    Stop-QuickTunnel
    $log = Join-Path $data "cloudflared.log"
    New-Item -ItemType Directory -Force $data | Out-Null
    Remove-Item $log -ErrorAction SilentlyContinue
    $tunnel = Start-Process cloudflared -PassThru -WindowStyle Hidden -ArgumentList @(
        "tunnel", "--no-autoupdate", "--url", "http://localhost:8080", "--logfile", "`"$log`""
    )
    Set-Content $pidFile $tunnel.Id
    $url = $null
    for ($i = 0; $i -lt 60 -and -not $url; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Path $log) {
            $match = Select-String -Path $log -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" |
                Select-Object -First 1
            if ($match) { $url = $match.Matches[0].Value }
        }
    }
    if (-not $url) { throw "The tunnel didn't report an address within 60 seconds. See $log" }
    $kind = "temporary address (changes on every start)"
} else {
    if (-not (Get-Command tailscale -ErrorAction SilentlyContinue)) {
        throw "Tailscale isn't installed. Install it from https://tailscale.com/download, or use -QuickTunnel."
    }
    $status = Get-TailscaleStatus
    if ($status.BackendState -ne "Running") {
        # On Windows the service waits for the tray app before it finishes starting.
        $gui = "C:\Program Files\Tailscale\tailscale-ipn.exe"
        if (Test-Path $gui) { Start-Process $gui }
        for ($i = 0; $i -lt 30 -and $status.BackendState -ne "Running"; $i++) {
            Start-Sleep -Seconds 2
            $status = Get-TailscaleStatus
        }
    }
    if ($status.BackendState -eq "NeedsLogin") { throw "Sign in to Tailscale first: run 'tailscale up'." }
    if ($status.BackendState -ne "Running") { throw "Tailscale isn't running (state: $($status.BackendState))." }

    # Idempotent; prints an approval link if Funnel isn't allowed for this tailnet yet.
    & tailscale funnel --bg 8080
    if ($LASTEXITCODE -ne 0) { throw "Couldn't turn on Tailscale Funnel (see the message above)." }
    $url = "https://" + $status.Self.DNSName.TrimEnd(".")
    $kind = "permanent address"

    $origins = Get-EnvValue "ALLOWED_ORIGINS"
    if (-not $origins -or -not $origins.Contains($url)) {
        Write-Host "Add $url to ALLOWED_ORIGINS in .env, then run: docker compose up -d" -ForegroundColor Yellow
    }
}

$code = Get-EnvValue "REGISTRATION_CODE"
Write-Host ""
Write-Host "LLMBook is shared at:  $url   ($kind)" -ForegroundColor Green
if ($code) { Write-Host "Invite code for sign-up: $code" -ForegroundColor Green }
else { Write-Host "Warning: no REGISTRATION_CODE in .env, so anyone with the link can sign up." -ForegroundColor Yellow }
Write-Host "Stop sharing with: powershell -ExecutionPolicy Bypass -File scripts\share.ps1 -Stop"
