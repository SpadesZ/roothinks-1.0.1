# Flow B Monitoring and Recovery Checklist (Repeatable)

Use this checklist to monitor and recover a stuck Slim literature run until Flow B reaches success.

## 0) Set runtime variables (PowerShell)

```powershell
$HostUrl = "http://127.0.0.1:10003"
$Container = "roothinks_progress_paq_v8"
$Pid = "ULQ8F6-p"
$PaperId = "Medium-Utterance_Baseline"
```

## 1) Confirm service/container health

```powershell
docker ps --format "table {{.Names}}`t{{.Status}}`t{{.Ports}}"
docker stats $Container --no-stream --format '{{.CPUPerc}} {{.MemUsage}} {{.NetIO}} {{.BlockIO}}'
```

Expected: app container is `Up` and port `127.0.0.1:10003->10003/tcp` is present.

## 2) Baseline status snapshot

```powershell
$r = Invoke-RestMethod -Uri "$HostUrl/api/literature/status/$Pid" -Method GET -TimeoutSec 20
$t = $r.papers | Where-Object { $_.paper_id -eq $PaperId } | Select-Object -First 1
$t | ConvertTo-Json -Depth 8
```

Record at least these fields:
- `flow_status`
- `db_status`
- `flow_a_ready`
- `flow_b_ready`
- `stages`

## 3) Ensure Flow A is running (or restart it)

Start Flow A:

```powershell
$body = @{ pid=$Pid; paper_ids=@($PaperId) } | ConvertTo-Json -Depth 6
Invoke-RestMethod -Uri "$HostUrl/api/literature/run_batch" -Method POST -ContentType 'application/json' -Body $body -TimeoutSec 30
```

If response says `Batch already running for this pid`, check stale lock:

```powershell
docker exec $Container sh -lc "stat /tmp/roothinks-locks/literature_active_${Pid}.lock 2>/dev/null || echo no-lock"
```

If lock is stale, remove it and restart Flow A:

```powershell
docker exec -u 0 $Container sh -lc "rm -rf /tmp/roothinks-locks/literature_active_${Pid}.lock"
Invoke-RestMethod -Uri "$HostUrl/api/literature/run_batch" -Method POST -ContentType 'application/json' -Body $body -TimeoutSec 30
```

## 4) Monitor Flow A until `ready_A`

```powershell
while ($true) {
  $r = Invoke-RestMethod -Uri "$HostUrl/api/literature/status/$Pid" -Method GET -TimeoutSec 20
  $t = $r.papers | Where-Object { $_.paper_id -eq $PaperId } | Select-Object -First 1
  "$(Get-Date -Format 'HH:mm:ss') flow=$($t.flow_status) db=$($t.db_status) A=$($t.flow_a_ready) B=$($t.flow_b_ready)"
  if ($t.flow_a_ready -eq $true) { break }
}
```

## 5) Optional emergency recovery if Flow A stalls on OCR

Use only when delivery is blocked and you accept a temporary rescue path.

Symptoms:
- long `processing_A`
- missing `text_4_raw.json` (or last page raw)
- no recent progress in runtime log

Check OCR outputs:

```powershell
docker exec $Container sh -lc 'ls -1 /app/data/'"$Pid"'/'"$PaperId"'/03_recognizes 2>/dev/null'
```

Emergency fallback example (copy last existing raw to missing raw):

```powershell
docker exec -u 0 $Container sh -lc 'cp -f /app/data/'"$Pid"'/'"$PaperId"'/03_recognizes/text_3_raw.json /app/data/'"$Pid"'/'"$PaperId"'/03_recognizes/text_4_raw.json'
```

Then rerun Step 3 and Step 4.

## 6) Start Flow B immediately after Flow A is ready

```powershell
$body = @{ pid=$Pid; paper_ids=@($PaperId) } | ConvertTo-Json -Depth 6
Invoke-RestMethod -Uri "$HostUrl/api/literature/run_translation" -Method POST -ContentType 'application/json' -Body $body -TimeoutSec 30
```

Expected response includes:
- `status: success`
- `queued` contains target paper

## 7) Monitor Flow B to terminal state

### 7.1 API poll loop

```powershell
while ($true) {
  $r = Invoke-RestMethod -Uri "$HostUrl/api/literature/status/$Pid" -Method GET -TimeoutSec 20
  $t = $r.papers | Where-Object { $_.paper_id -eq $PaperId } | Select-Object -First 1
  "$(Get-Date -Format 'HH:mm:ss') flow=$($t.flow_status) db=$($t.db_status) A=$($t.flow_a_ready) B=$($t.flow_b_ready)"
  if ($t.flow_b_ready -eq $true -or $t.db_status -eq 'failed') { break }
}
```

### 7.2 Runtime log watcher (optional, immediate signal)

```powershell
docker exec $Container sh -lc "tail -n 0 -F /app/data/_logs/system/runtime.log | grep -m 1 -E '$PaperId -> (ready_B|failed)'"
```

## 8) Success criteria (all should pass)

1. API status:
   - `flow_status = ready_B`
   - `db_status = ready_B`
   - `flow_b_ready = true`
2. Output artifact exists:

```powershell
docker exec $Container sh -lc "ls -l /app/data/$Pid/$PaperId/06_translates/fusion/full_text_trans.json"
```

3. Runtime log has target ready event:

```powershell
docker exec $Container sh -lc "grep -E '$PaperId -> ready_B' /app/data/_logs/system/runtime.log | tail -n 1"
```

## 9) If Flow B is still not successful

1. Re-trigger Flow B once:

```powershell
Invoke-RestMethod -Uri "$HostUrl/api/literature/run_translation" -Method POST -ContentType 'application/json' -Body $body -TimeoutSec 30
```

2. Re-check status + output file + logs.
3. If still stuck, collect focused logs:

```powershell
docker exec $Container sh -lc "grep -Ei '$PaperId|processing_B|translating_local|judging_llm|ready_B|failed|timeout' /app/data/_logs/system/runtime.log | tail -n 200"
```

## 10) Guardrails

- Keep Slim semantics independent; do not map Slim routes to LITE task numbering.
- Do not treat old `ready_B` entries as current success; always verify latest timestamp and output artifact.
- If multiple Flow B workers are active, final truth must come from latest API + latest target-paper log line + output file presence.
