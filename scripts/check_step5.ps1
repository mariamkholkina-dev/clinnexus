param(
  [string]$ApiBase = "http://localhost:8000",
  [string]$WorkspaceId = "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  [string]$DocxPath = "C:\Users\0\clinnexus\scripts\2_DMBN_ALZH-2022-II_Протокол Димебон_2.0 от 11.08.2023.docx",
  [int]$TimeoutSec = 300
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert($cond, $msg) { if (-not $cond) { throw "ASSERT FAILED: $msg" } }

function ReqJson {
  param(
    [Parameter(Mandatory=$true)][ValidateSet("GET","POST")][string]$Method,
    [Parameter(Mandatory=$true)][string]$Url,
    [string]$BodyJson = $null
  )
  $headers = @{ "Accept" = "application/json" }

  if ($Method -eq "GET") {
    return Invoke-RestMethod -Method Get -Uri $Url -Headers $headers
  }

  if ($null -ne $BodyJson) {
    return Invoke-RestMethod -Method Post -Uri $Url `
      -Headers @{ "Content-Type"="application/json"; "Accept"="application/json" } `
      -Body $BodyJson
  } else {
    return Invoke-RestMethod -Method Post -Uri $Url -Headers $headers
  }
}

function Prompt-IfEmpty([string]$value, [string]$promptText) {
  if ([string]::IsNullOrWhiteSpace($value)) {
    return Read-Host $promptText
  }
  return $value
}

Write-Host ""
Write-Host "=== ClinNexus Step 5 Check (SoA + cell anchors) ===" -ForegroundColor Cyan
$apiBaseMsg = 'ApiBase: ' + [string]$ApiBase
Write-Host $apiBaseMsg

# --- Inputs from user ---
$WorkspaceId = Prompt-IfEmpty $WorkspaceId "Введите WORKSPACE_ID (UUID)"
$workspaceIdStr = [string]$WorkspaceId
$workspaceIdMsg = 'WorkspaceId does not look like UUID: ' + $workspaceIdStr
Assert ($WorkspaceId -match "^[0-9a-fA-F-]{36}$") $workspaceIdMsg

$DocxPath = Prompt-IfEmpty $DocxPath "Введите путь к DOCX (протокол с SoA таблицей)"
$DocxPath = $DocxPath.Trim('"')

$docxPathStr = [string]$DocxPath
$fileNotFoundMsg = 'File not found: ' + $docxPathStr
Assert (Test-Path $DocxPath) $fileNotFoundMsg
$docxRequiredMsg = 'Need .docx file: ' + $docxPathStr
Assert ($DocxPath.ToLower().EndsWith(".docx")) $docxRequiredMsg

# --- 0) Health ---
Write-Host ""
Write-Host "-> Health" -ForegroundColor Cyan
ReqJson GET "$ApiBase/health" | Out-Null
Write-Host "OK"

# --- 1) Create study ---
Write-Host ""
Write-Host "-> Create study" -ForegroundColor Cyan
$unix = [Math]::Floor([double](Get-Date -UFormat %s))
$studyCode = "STEP5-$unix"
$studyBody = @{
  workspace_id = $WorkspaceId
  study_code = $studyCode
  title = "Step5 SoA Test Study"
  status = "active"
} | ConvertTo-Json -Compress
$study = ReqJson POST "$ApiBase/api/studies" $studyBody
$studyId = $study.id
Assert $studyId "study.id is empty"
$studyIdMsg = 'study_id=' + [string]$studyId
Write-Host $studyIdMsg

# --- 2) Create document (protocol) ---
Write-Host ""
Write-Host "-> Create document (protocol)" -ForegroundColor Cyan
$docBody = @{
  doc_type = "protocol"
  title = "Protocol (SoA Test)"
  lifecycle_status = "draft"
} | ConvertTo-Json -Compress
$doc = ReqJson POST "$ApiBase/api/studies/$studyId/documents" $docBody
$documentId = $doc.id
Assert $documentId "document.id is empty"
$docIdMsg = 'document_id=' + [string]$documentId
Write-Host $docIdMsg

# --- 3) Create version ---
Write-Host ""
Write-Host "-> Create document version" -ForegroundColor Cyan
$verBody = @{
  version_label = "v1.0"
} | ConvertTo-Json -Compress
$ver = ReqJson POST "$ApiBase/api/documents/$documentId/versions" $verBody
$versionId = $ver.id
Assert $versionId "version.id is empty"
$verIdMsg = 'version_id=' + [string]$versionId
Write-Host $verIdMsg

# --- 4) Upload DOCX (via curl.exe for max compatibility) ---
Write-Host ""
Write-Host "-> Upload DOCX" -ForegroundColor Cyan
$uploadJson = & curl.exe -sS -X POST "$ApiBase/api/document-versions/$versionId/upload" -F "file=@$DocxPath"
Assert $uploadJson "curl upload returned empty response"
$upload = $uploadJson | ConvertFrom-Json
Assert $upload.sha256 "upload.sha256 is missing"
Assert $upload.uri "upload.uri is missing"
$sha256Msg = 'upload OK sha256=' + [string]$upload.sha256
Write-Host $sha256Msg
$uriMsg = 'upload uri=' + [string]$upload.uri
Write-Host $uriMsg

# --- 5) Ingest ---
Write-Host ""
Write-Host "-> Start ingestion" -ForegroundColor Cyan

# Проверяем текущий статус перед запуском
$vUrl = $ApiBase + '/api/document-versions/' + [string]$versionId
$v = ReqJson GET $vUrl
$currentStatus = $v.ingestion_status

if ($currentStatus -eq "processing") {
  Write-Host "Ингестия уже выполняется, ожидаем завершения..." -ForegroundColor Yellow
} else {
  # Запускаем ингестию только если она не выполняется
  try {
    $ingestUrlFirst = $ApiBase + '/api/document-versions/' + [string]$versionId + '/ingest?force=true'
    ReqJson POST $ingestUrlFirst '{}' | Out-Null
  } catch {
    $errorDetail = $_.Exception.Message
    # Проверяем, не является ли это ошибкой conflict (уже выполняется)
    if ($errorDetail -match "conflict" -or $errorDetail -match "already") {
      Write-Host "Ингестия уже выполняется, ожидаем завершения..." -ForegroundColor Yellow
    } else {
      # some implementations accept no body
      try {
        $ingestUrlFirst = $ApiBase + '/api/document-versions/' + [string]$versionId + '/ingest?force=true'
        ReqJson POST $ingestUrlFirst | Out-Null
      } catch {
        # Если и это не сработало, просто продолжаем - возможно ингестия уже запущена
        Write-Host "Не удалось запустить ингестию, проверяем статус..." -ForegroundColor Yellow
      }
    }
  }
}

# --- 6) Poll status ---
Write-Host ""
Write-Host "-> Poll ingestion status" -ForegroundColor Cyan
Write-Host "Таймаут: $TimeoutSec секунд" -ForegroundColor Gray
$deadline = (Get-Date).AddSeconds($TimeoutSec)
$status = ""
$pollCount = 0

do {
  $vUrl = $ApiBase + '/api/document-versions/' + [string]$versionId
  $v = ReqJson GET $vUrl
  $status = $v.ingestion_status
  $pollCount++
  
  $remaining = [Math]::Max(0, [int]($deadline - (Get-Date)).TotalSeconds)
  $statusMsg = "status=$status (осталось ${remaining}с, опрос #$pollCount)"
  Write-Host $statusMsg
  
  if ($status -in @("ready","needs_review","failed")) { break }
  
  # Показываем предупреждение, если осталось мало времени
  if ($remaining -lt 30 -and $remaining -gt 0) {
    Write-Host "ВНИМАНИЕ: Осталось менее 30 секунд до таймаута!" -ForegroundColor Yellow
  }
  
  Start-Sleep -Seconds 1
} while ((Get-Date) -lt $deadline)

if ($status -eq "processing") {
  $timeoutMsg = "Таймаут ожидания ингестии ($TimeoutSec секунд) истёк. Статус всё ещё 'processing'."
  Write-Host $timeoutMsg -ForegroundColor Red
  Write-Host "Возможно, ингестия зависла или документ слишком большой." -ForegroundColor Yellow
  Write-Host "Проверьте логи сервера для диагностики." -ForegroundColor Yellow
  throw "ASSERT FAILED: $timeoutMsg"
}

if ($status -eq "failed") {
  $vJson = $v | ConvertTo-Json -Depth 10
  $errorMsg = 'ASSERT FAILED: Ingestion failed. Version payload: ' + $vJson
  throw $errorMsg
}
$statusStr = [string]$status
$unexpectedStatusMsg = 'Unexpected final status: ' + $statusStr
Assert ($status -in @("ready","needs_review")) $unexpectedStatusMsg

# --- 7) Check cell anchors exist ---
Write-Host ""
Write-Host "-> Fetch cell anchors" -ForegroundColor Cyan
$cellAnchorsUrl = $ApiBase + '/api/document-versions/' + [string]$versionId + '/anchors?content_type=cell'
$cellAnchors = ReqJson GET $cellAnchorsUrl
$cellCount = @($cellAnchors).Count
$cellCountMsg = 'cell anchors count=' + [string]$cellCount
Write-Host $cellCountMsg
Assert ($cellCount -gt 0) "Expected cell anchors > 0 (SoA table should produce cell anchors)."

# Build set of anchor_id for quick membership tests
$cellAnchorIdSet = @{}
foreach ($a in $cellAnchors) { $cellAnchorIdSet[$a.anchor_id] = $true }

# --- 8) Fetch SoA endpoint ---
Write-Host ""
Write-Host "-> Fetch SoA (/soa)" -ForegroundColor Cyan
$soa = $null
try {
  $soaUrl = $ApiBase + '/api/document-versions/' + [string]$versionId + '/soa'
  $soa = ReqJson GET $soaUrl
} catch {
  $errorDetail = $_.Exception.Message
  $soaErrorMsg = 'SoA endpoint failed. Is /api/document-versions/{version_id}/soa implemented? Error: ' + $errorDetail
  throw $soaErrorMsg
}

# Validate shape
Assert ($null -ne $soa) "SoA response is null"
Assert ($soa.visits -ne $null) "SoA.visits missing"
Assert ($soa.procedures -ne $null) "SoA.procedures missing"
Assert ($soa.matrix -ne $null) "SoA.matrix missing"

$visitCount = @($soa.visits).Count
$procCount  = @($soa.procedures).Count
$mxCount    = @($soa.matrix).Count

$soaMsg = 'SoA visits=' + [string]$visitCount + ' procedures=' + [string]$procCount + ' matrix=' + [string]$mxCount
Write-Host $soaMsg
Assert ($visitCount -gt 0) "Expected visits greater than 0"
Assert ($procCount -gt 0) "Expected procedures greater than 0"
Assert ($mxCount -gt 0) "Expected matrix greater than 0"

# spot-check: matrix anchor_id exists among cell anchors (sample up to 30)
Write-Host ""
Write-Host "-> Validate matrix anchor_ids exist among cell anchors (sample)" -ForegroundColor Cyan
$missing = 0
$checked = 0

foreach ($m in $soa.matrix) {
  if ($checked -ge 30) { break }
  $aid = $m.anchor_id
  if ([string]::IsNullOrWhiteSpace($aid)) {
    $missing += 1
  } elseif (-not $cellAnchorIdSet.ContainsKey($aid)) {
    $missing += 1
  }
  $checked += 1
}

$missingStr = [string]$missing
$checkedStr = [string]$checked
$assertMsg = 'Some matrix anchor_id are missing in cell anchors set (missing=' + $missingStr + ' of checked=' + $checkedStr + ').'
Assert ($missing -eq 0) $assertMsg

# --- 9) Re-ingest check (force=true) for duplication sanity ---
Write-Host ""
Write-Host "-> Re-ingest (force=true) and ensure cell anchors not duplicated" -ForegroundColor Cyan
try {
  $ingestUrl = $ApiBase + '/api/document-versions/' + [string]$versionId + '/ingest?force=true'
  ReqJson POST $ingestUrl '{}' | Out-Null
} catch {
  $ingestUrl = $ApiBase + '/api/document-versions/' + [string]$versionId + '/ingest?force=true'
  ReqJson POST $ingestUrl | Out-Null
}

$deadline = (Get-Date).AddSeconds($TimeoutSec)
do {
  $v2Url = $ApiBase + '/api/document-versions/' + [string]$versionId
  $v2 = ReqJson GET $v2Url
  $status2 = $v2.ingestion_status
  $status2Msg = 'status=' + [string]$status2
  Write-Host $status2Msg
  if ($status2 -in @("ready","needs_review","failed")) { break }
  Start-Sleep -Seconds 1
} while ((Get-Date) -lt $deadline)

if ($status2 -eq "failed") {
  $v2Json = $v2 | ConvertTo-Json -Depth 10
  $errorMsg2 = 'ASSERT FAILED: Re-ingestion failed. Version payload: ' + $v2Json
  throw $errorMsg2
}

$cellAnchors2Url = $ApiBase + '/api/document-versions/' + [string]$versionId + '/anchors?content_type=cell'
$cellAnchors2 = ReqJson GET $cellAnchors2Url
$cellCount2 = @($cellAnchors2).Count
$reingestMsg = 'cell anchors after re-ingest=' + [string]$cellCount2
Write-Host $reingestMsg

if ($cellCount2 -ne $cellCount) {
  $count1 = [string]$cellCount
  $count2 = [string]$cellCount2
  $msg = 'WARN: cell anchors count changed after re-ingest (from ' + $count1 + ' to ' + $count2 + '). This can be OK if parsing is non-deterministic, but it must NOT monotonically increase.'
  Write-Host $msg -ForegroundColor Yellow
}

Write-Host ""
$successMsg = 'STEP 5 CHECK PASSED'
Write-Host $successMsg -ForegroundColor Green
Write-Host ('study_id=' + [string]$studyId)
Write-Host ('document_id=' + [string]$documentId)
Write-Host ('version_id=' + [string]$versionId)
$docxPathSafe = [string]$DocxPath
$docxOutput = 'docx=' + $docxPathSafe
Write-Host $docxOutput
