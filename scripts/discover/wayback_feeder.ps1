# Wayback ATS tenant feeder.
# Harvests ATS tenant/board lists from the Internet Archive Wayback CDX API and writes
# one data/wayback-ats/{ats}.csv per provider (columns: ats,tenant,url).
#
# Output is candidate-grade: Wayback is historical, so lists include dead/parked boards and
# some noise. Validate (Stage-2 probe) before trusting a tenant as live.
#
# Re-run:  powershell -File scripts/discover/wayback_feeder.ps1

$root   = if ($PSScriptRoot) { Split-Path -Parent $PSScriptRoot } else { "C:\Users\jains\Desktop\HeadStart" }
$outDir = Join-Path $root "data\wayback-ats"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$infra = @('www','app','apps','blog','support','help','api','status','smtp','mail','email',
           'cdn','assets','static','go','info','docs','careers','jobs','admin','portal','test',
           'staging','dev','demo','about','home','login','secure','my','www2','m','en','impl')

# style: sub = {tenant}.{domain} | path = host/{tenant} | workday = {tenant}.{dc}.host
$providers = @(
  # Zoho: Wayback deep-crawled few tenants (deep-but-narrow), so it under-covers Zoho badly.
  # Use the Common Crawl miner (data/discover/india_ats_tenants.csv, ~2.9k zoho) as the source instead.
  @{ ats='zoho';         style='sub';     targets=@('zohorecruit.com') },
  @{ ats='darwinbox';    style='sub';     targets=@('darwinbox.in','darwinbox.com') },
  @{ ats='keka';         style='sub';     targets=@('keka.com') },
  @{ ats='ripplehire';   style='sub';     targets=@('ripplehire.com') },
  @{ ats='turbohire';    style='sub';     targets=@('turbohire.com','turbohire.co') },
  @{ ats='qandle';       style='sub';     targets=@('qandle.com') },
  @{ ats='beehive';      style='sub';     targets=@('beehivehcm.com') },
  @{ ats='workable';     style='path';    targets=@('apply.workable.com') },
  @{ ats='recruitee';    style='sub';     targets=@('recruitee.com') },
  @{ ats='greenhouse';   style='path';    targets=@('boards.greenhouse.io','job-boards.greenhouse.io') },
  @{ ats='lever';        style='path';    targets=@('jobs.lever.co') },
  @{ ats='ashby';        style='path';    targets=@('jobs.ashbyhq.com') },
  @{ ats='workday';      style='workday'; targets=@('myworkdayjobs.com') }
)

function Get-CdxUrls($target) {
  $u = "https://web.archive.org/cdx/search/cdx?url=$target&matchType=domain&fl=original&collapse=urlkey&output=text&limit=50000"
  try {
    $resp = Invoke-WebRequest -Uri $u -TimeoutSec 150 -UseBasicParsing -ErrorAction Stop
    return ($resp.Content -split "`n") | Where-Object { $_ }
  } catch {
    Write-Output "  WARN $target failed: $($_.Exception.Message)"
    return @()
  }
}

function Test-Label($l) {
  return ($l -match '^[a-z0-9][a-z0-9-]{1,62}$') -and ($infra -notcontains $l)
}

$only = @($args | ForEach-Object { "$_".ToLower() })   # optional: harvest only named providers
foreach ($p in $providers) {
  if ($only.Count -and ($only -notcontains $p.ats)) { continue }
  $set = @{}
  foreach ($t in $p.targets) {
    foreach ($url in (Get-CdxUrls $t)) {
      if ($url -notmatch '^https?://([^/]+)(/[^ ]*)?') { continue }
      $h = $matches[1].ToLower(); $path = $matches[2]
      switch ($p.style) {
        'sub' {
          if ($h -like "*.$t") {
            $label = $h -replace ('\.' + [regex]::Escape($t) + '$'),''
            if ($label -notmatch '\.' -and (Test-Label $label)) { $set[$label] = "https://$label.$t" }
          }
        }
        'path' {
          if ($h -eq $t -and $path) {
            $seg = (($path.TrimStart('/') -split '/')[0] -split '\?')[0].ToLower()
            if ((Test-Label $seg) -and $seg -ne 'embed') { $set[$seg] = "https://$t/$seg" }
          }
        }
        'workday' {
          if ($h -like "*.$t") {
            $label = ($h -split '\.')[0]
            if (Test-Label $label) { $set[$label] = "https://$h" }
          }
        }
      }
    }
  }
  $rows = $set.GetEnumerator() | Sort-Object Name | ForEach-Object { "$($p.ats),$($_.Key),$($_.Value)" }
  $outFile = Join-Path $outDir "$($p.ats).csv"
  [System.IO.File]::WriteAllLines($outFile, (@("ats,tenant,url") + $rows), [System.Text.UTF8Encoding]::new($false))
  Write-Output "$($p.ats): $($rows.Count) tenants -> data/wayback-ats/$($p.ats).csv"
}
Write-Output "DONE"
