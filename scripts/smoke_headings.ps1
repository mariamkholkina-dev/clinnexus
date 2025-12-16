param(
  [string]$ApiBase = "http://localhost:8000",
  [string]$WorkspaceId = "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  [string]$DocxPath = "CNS-CD0080045-06_Protocol_pre-final.docx",
  [int]$TimeoutSec = 120
)

# Установка кодировки для корректного вывода кириллицы
$PSDefaultParameterValues['*:Encoding'] = 'utf8'
$OutputEncoding = [System.Text.Encoding]::UTF8
$InputEncoding = [System.Text.Encoding]::UTF8

# Попытка установить UTF-8 кодировку консоли
try {
    $codePage = [System.Text.Encoding]::UTF8.CodePage
    $null = cmd /c "chcp $codePage >nul 2>&1"
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    [Console]::InputEncoding = [System.Text.Encoding]::UTF8
} catch {
    # Если UTF-8 не поддерживается, пробуем Windows-1251 для русской локали
    try {
        [Console]::OutputEncoding = [System.Text.Encoding]::GetEncoding(1251)
        [Console]::InputEncoding = [System.Text.Encoding]::GetEncoding(1251)
    } catch {}
}

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert($cond, $msg) { if (-not $cond) { throw ('ASSERT FAILED: ' + $msg) } }

function ReqJson {
  param(
    [Parameter(Mandatory=$true)][ValidateSet("GET","POST")][string]$Method,
    [Parameter(Mandatory=$true)][string]$Url,
    [string]$BodyJson = $null
  )
  $headers = @{ 'Accept' = 'application/json' }
  if ($Method -eq 'GET') {
    return Invoke-RestMethod -Method Get -Uri $Url -Headers $headers
  }
  if ($null -ne $BodyJson) {
    return Invoke-RestMethod -Method Post -Uri $Url `
      -Headers @{ 'Content-Type'='application/json'; 'Accept'='application/json' } `
      -Body $BodyJson
  }
  return Invoke-RestMethod -Method Post -Uri $Url -Headers $headers
}

function Prompt-IfEmpty([string]$value, [string]$promptText) {
  if ([string]::IsNullOrWhiteSpace($value)) {
    return Read-Host $promptText
  }
  return $value
}

Write-Host ""
Write-Host '=== Heading Smoke (PS 5.1) ===' -ForegroundColor Cyan
Write-Host ('ApiBase: ' + $ApiBase)

$WorkspaceId = Prompt-IfEmpty $WorkspaceId 'Введите WORKSPACE_ID (UUID)'
Assert ($WorkspaceId -match '^[0-9a-fA-F-]{36}$') ('WorkspaceId не похож на UUID: ' + $WorkspaceId)

$DocxPath = Prompt-IfEmpty $DocxPath 'Введите путь к DOCX протоколу'
$DocxPath = $DocxPath.Trim('"')
Assert (Test-Path $DocxPath) ('Файл не найден: ' + $DocxPath)
Assert ($DocxPath.ToLower().EndsWith('.docx')) ('Нужен .docx файл: ' + $DocxPath)

# health
Write-Host ""
Write-Host '-> Health' -ForegroundColor Cyan
ReqJson -Method GET -Url ($ApiBase + '/health') | Out-Null
Write-Host 'OK'

# create study
Write-Host ""
Write-Host '-> Create study/document/version' -ForegroundColor Cyan
$unix = [long][Math]::Floor((Get-Date -UFormat %s))
$studyCode = "HDRSMOKE-$unix"

$studyBody = (@{
  workspace_id = $WorkspaceId
  study_code = $studyCode
  title = "Heading Smoke Study"
  status = "active"
} | ConvertTo-Json -Compress)
$study = ReqJson -Method POST -Url ($ApiBase + '/api/studies') -BodyJson $studyBody
$studyId = $study.id
Assert $studyId 'study.id empty'
Write-Host ('study_id=' + $studyId)

$docBody = (@{
  doc_type = "protocol"
  title = "Protocol (Heading Smoke)"
  lifecycle_status = "draft"
} | ConvertTo-Json -Compress)
$doc = ReqJson -Method POST -Url ($ApiBase + '/api/studies/' + $studyId + '/documents') -BodyJson $docBody
$documentId = $doc.id
Assert $documentId 'document.id empty'
Write-Host ('document_id=' + $documentId)

$verBody = (@{
  version_label = "v1.0"
} | ConvertTo-Json -Compress)
$ver = ReqJson -Method POST -Url ($ApiBase + '/api/documents/' + $documentId + '/versions') -BodyJson $verBody
$versionId = $ver.id
Assert $versionId 'version.id empty'
Write-Host ('version_id=' + $versionId)

# upload (curl.exe for PS5.1 compatibility)
Write-Host ""
Write-Host '-> Upload DOCX' -ForegroundColor Cyan
$uploadJson = & curl.exe -sS -X POST ($ApiBase + '/api/document-versions/' + $versionId + '/upload') -F ('file=@' + $DocxPath)
Assert $uploadJson 'Upload returned empty response'
$upload = $uploadJson | ConvertFrom-Json
Assert $upload.sha256 'upload.sha256 missing'
Write-Host ('upload OK sha256=' + $upload.sha256)

# ingest
Write-Host ""
Write-Host '-> Ingest + poll' -ForegroundColor Cyan
try { ReqJson -Method POST -Url ($ApiBase + '/api/document-versions/' + $versionId + '/ingest') -BodyJson (@{} | ConvertTo-Json -Compress) | Out-Null }
catch { ReqJson -Method POST -Url ($ApiBase + '/api/document-versions/' + $versionId + '/ingest') | Out-Null }

$deadline = (Get-Date).AddSeconds($TimeoutSec)
$status = ""
do {
  $v = ReqJson -Method GET -Url ($ApiBase + '/api/document-versions/' + $versionId)
  $status = $v.ingestion_status
  Write-Host ('status=' + $status)
  if ($status -in @('ready','needs_review','failed')) { break }
  Start-Sleep -Seconds 1
} while ((Get-Date) -lt $deadline)

if ($status -eq 'failed') {
  Write-Host 'INGEST FAILED:' -ForegroundColor Red
  Write-Host ($v | ConvertTo-Json -Depth 10)
  exit 1
}

# print heading metrics if present
Write-Host ""
Write-Host '-> Ingestion heading metrics (if present)' -ForegroundColor Cyan
if ($null -ne $v.ingestion_summary_json) {
  $s = $v.ingestion_summary_json

  if ($s.PSObject.Properties.Name -contains 'heading_detected_count') {
    Write-Host ('heading_detected_count: ' + $s.heading_detected_count)
  }
  if ($s.PSObject.Properties.Name -contains 'heading_quality') {
    Write-Host ('heading_quality: ' + $s.heading_quality)
  }
  if ($s.PSObject.Properties.Name -contains 'heading_levels_histogram') {
    Write-Host 'heading_levels_histogram:'
    $s.heading_levels_histogram | ConvertTo-Json -Depth 6 | Write-Host
  }
  if ($s.PSObject.Properties.Name -contains 'heading_detection_mode_counts') {
    Write-Host 'heading_detection_mode_counts:'
    $s.heading_detection_mode_counts | ConvertTo-Json -Depth 6 | Write-Host
  }
  if ($s.PSObject.Properties.Name -contains 'warnings') {
    Write-Host 'warnings:'
    $s.warnings | ConvertTo-Json -Depth 6 | Write-Host
  }
} else {
  Write-Host 'No ingestion_summary_json found on version.' -ForegroundColor Yellow
}

# fetch hdr anchors
Write-Host ""
Write-Host '-> Fetch hdr anchors' -ForegroundColor Cyan
$hdrAnchors = ReqJson -Method GET -Url ($ApiBase + '/api/document-versions/' + $versionId + '/anchors?content_type=hdr')
$hdrCount = @($hdrAnchors).Count
Write-Host ('hdr anchors count=' + $hdrCount)

if ($hdrCount -eq 0) {
  Write-Host 'No hdr anchors. Likely heading detection failed; check ingestion_summary_json warnings.' -ForegroundColor Yellow
  Write-Host ('version_id=' + $versionId)
  exit 2
}

# print top headings (by ordinal order)
Write-Host ""
Write-Host '-> Top headings (first 30)' -ForegroundColor Cyan
$top = @($hdrAnchors | Select-Object -First 30)

$idx = 0
foreach ($h in $top) {
  $idx++
  # show section_path + preview + location_json style hint if present
  $preview = $h.text_raw
  if ($preview.Length -gt 90) { $preview = $preview.Substring(0,90) + '…' }

  $style = ""
  try {
    if ($null -ne $h.location_json -and ($h.location_json.PSObject.Properties.Name -contains 'style')) {
      $style = $h.location_json.style
    }
  } catch {}

  Write-Host ('[{0}] {1} | ord={2} | style={3} | {4}' -f $idx, $h.section_path, $h.ordinal, $style, $preview)
}

# top section_path frequencies
Write-Host ""
Write-Host '-> Top section_path (hdr frequency)' -ForegroundColor Cyan
$grouped = $hdrAnchors | Group-Object -Property section_path | Sort-Object Count -Descending | Select-Object -First 15
foreach ($g in $grouped) {
  Write-Host ('{0}  ({1})' -f $g.Name, $g.Count)
}

Write-Host ""
Write-Host '✅ Heading smoke done' -ForegroundColor Green
Write-Host ('version_id=' + $versionId)
Write-Host ('docx=' + $DocxPath)
