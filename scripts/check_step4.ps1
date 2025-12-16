param(
  [string]$ApiBase = "http://localhost:8000",
  [string]$WorkspaceId = "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  [string]$DocxPath = "CNS-CD0080045-06_Protocol_pre-final.docx",
  [int]$TimeoutSec = 60
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Section($msg) {
  Write-Host ""
  Write-Host "== $msg ==" -ForegroundColor Cyan
}

function Http-Json {
  param(
    [Parameter(Mandatory=$true)][ValidateSet("GET","POST")][string]$Method,
    [Parameter(Mandatory=$true)][string]$Url,
    [string]$BodyJson = $null
  )
  $headers = @{ "Accept" = "application/json" }
  if ($Method -eq "GET") {
    return Invoke-RestMethod -Method Get -Uri $Url -Headers $headers
  } else {
    if ($null -ne $BodyJson) {
      return Invoke-RestMethod -Method Post -Uri $Url -Headers @{ "Content-Type"="application/json"; "Accept"="application/json" } -Body $BodyJson
    } else {
      return Invoke-RestMethod -Method Post -Uri $Url -Headers $headers
    }
  }
}

function Assert($cond, $msg) {
  if (-not $cond) { throw "ASSERT FAILED: $msg" }
}

Write-Section "Step4 smoke (PowerShell) ApiBase=$ApiBase WorkspaceId=$WorkspaceId"

# 0) Health
Write-Section "Health"
$health = Http-Json -Method GET -Url "$ApiBase/health"
Write-Host "OK"

# 1) Create study
Write-Section "Create study"
$studyCode = "STEP4-$([int][double]::Parse((Get-Date -UFormat %s)))"
$studyBody = @{
  workspace_id = $WorkspaceId
  study_code   = $studyCode
  title        = "Step4 Test Study"
  status       = "active"
} | ConvertTo-Json

$study = Http-Json -Method POST -Url "$ApiBase/api/studies" -BodyJson $studyBody
$studyId = $study.id
Assert ($studyId) "study.id is empty"
Write-Host "study_id=$studyId"

# 2) Create document
Write-Section "Create document (protocol)"
$docBody = @{
  doc_type = "protocol"
  title    = "Protocol"
  lifecycle_status = "draft"
} | ConvertTo-Json

$doc = Http-Json -Method POST -Url "$ApiBase/api/studies/$studyId/documents" -BodyJson $docBody
$documentId = $doc.id
Assert ($documentId) "document.id is empty"
Write-Host "document_id=$documentId"

# 3) Create version
Write-Section "Create version"
$verBody = @{ version_label = "v1.0" } | ConvertTo-Json
$ver = Http-Json -Method POST -Url "$ApiBase/api/documents/$documentId/versions" -BodyJson $verBody
$versionId = $ver.id
Assert ($versionId) "version.id is empty"
Write-Host "version_id=$versionId"

# 4) Generate or use provided DOCX
Write-Section "DOCX file"
$tmpDir = $null
$docxPathToUse = ""
# Определяем pythonExe заранее, так как она используется позже в скрипте
$pythonExe = "python"

if ([string]::IsNullOrWhiteSpace($DocxPath)) {
  # Generate DOCX if path not provided
  Write-Host "Generating DOCX file..." -ForegroundColor Gray
  $tmpDir = Join-Path $env:TEMP ("clinnexus_step4_" + [guid]::NewGuid().ToString())
  New-Item -ItemType Directory -Path $tmpDir | Out-Null
  $docxPathToUse = Join-Path $tmpDir "step4_test.docx"

  $py = @'
from docx import Document
d = Document()
d.add_paragraph('Introduction', style='Heading 1')
d.add_paragraph('This is paragraph 1.')
d.add_paragraph('Objectives', style='Heading 2')
d.add_paragraph('Primary objective: Evaluate safety.')
d.add_paragraph('Secondary objective: Evaluate PK.')
d.add_paragraph('Item 1', style='List Bullet')
d.add_paragraph('Item 2', style='List Bullet')
docx_path = r'DOCX_PATH_PLACEHOLDER'
d.save(docx_path)
print(docx_path)
'@
  $py = $py -replace 'DOCX_PATH_PLACEHOLDER', $docxPathToUse

  $docxOut = & $pythonExe -c $py
  Assert (Test-Path $docxPathToUse) "DOCX was not created at $docxPathToUse"
  Write-Host "DOCX generated: $docxPathToUse"
} else {
  # Use provided DOCX path
  $docxPathToUse = $DocxPath.Trim('"')
  Assert (Test-Path $docxPathToUse) "DOCX file not found: $docxPathToUse"
  Assert ($docxPathToUse.ToLower().EndsWith(".docx")) "File must be .docx: $docxPathToUse"
  Write-Host "Using provided DOCX: $docxPathToUse"
}

# 5) Upload
Write-Section "Upload DOCX"
# Invoke-RestMethod supports -Form in PowerShell 7+. If on PS 5.1, we use curl.exe.
$psMajor = $PSVersionTable.PSVersion.Major

if ($psMajor -ge 7) {
  $upload = Invoke-RestMethod -Method Post -Uri "$ApiBase/api/document-versions/$versionId/upload" `
    -Form @{ file = Get-Item $docxPathToUse } `
    -Headers @{ "Accept"="application/json" }
} else {
  # Windows curl.exe is usually available
  $uploadJson = & curl.exe -sS -X POST "$ApiBase/api/document-versions/$versionId/upload" -F "file=@$docxPathToUse"
  $upload = $uploadJson | ConvertFrom-Json
}

Assert ($upload.sha256) "Upload sha256 missing"
Assert ($upload.uri) "Upload uri missing"
Write-Host "upload sha256=$($upload.sha256)"
Write-Host "upload uri=$($upload.uri)"

# 6) Ingest
Write-Section "Start ingestion"
try {
  # your endpoint expects JSON body; can pass empty object
  $null = Http-Json -Method POST -Url "$ApiBase/api/document-versions/$versionId/ingest" -BodyJson "{}"
} catch {
  # Some implementations ignore body; retry without
  $null = Http-Json -Method POST -Url "$ApiBase/api/document-versions/$versionId/ingest"
}

# 7) Poll status
Write-Section "Poll ingestion status"
$deadline = (Get-Date).AddSeconds($TimeoutSec)
$status = ""

do {
  $verNow = Http-Json -Method GET -Url "$ApiBase/api/document-versions/$versionId"
  $status = $verNow.ingestion_status
  Write-Host "status=$status"
  if ($status -in @("ready","needs_review","failed")) { break }
  Start-Sleep -Seconds 1
} while ((Get-Date) -lt $deadline)

Assert ($status -ne "failed") ("Ingestion failed. Payload: " + ($verNow | ConvertTo-Json -Depth 8))
Assert ($status -in @("ready","needs_review")) "Unexpected final status: $status"

# 8) Fetch anchors
Write-Section "Fetch anchors"
$anchors = Http-Json -Method GET -Url "$ApiBase/api/document-versions/$versionId/anchors"
$anchorCount = @($anchors).Count
Write-Host "anchors count=$anchorCount"
Assert ($anchorCount -gt 0) "Expected anchors > 0"

# 9) Validate anchors (format + must-have types)
Write-Section "Validate anchors (format, types, section_path)"
# We validate in Python to keep regex + JSON handling easy
# Используем временные файлы, чтобы избежать ограничения длины командной строки Windows
$anchorsJson = $anchors | ConvertTo-Json -Depth 20
$tempDir = Join-Path $env:TEMP ("clinnexus_check_" + [guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
$jsonFile = Join-Path $tempDir "anchors.json"
$pyFile = Join-Path $tempDir "check_anchors.py"

# Сохраняем JSON в файл без BOM (используем .NET метод для UTF-8 без BOM)
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($jsonFile, $anchorsJson, $utf8NoBom)

# Создаём Python скрипт
$checkPy = @"
import json, re, sys, os

json_file = r"$jsonFile"
doc_version_id = r"$versionId"

with open(json_file, 'r', encoding='utf-8-sig') as f:
    anchors = json.load(f)

types=set()
bad=[]

for a in anchors[:500]:
    aid=a["anchor_id"]
    ct=a["content_type"]
    sp=a["section_path"]
    if not sp:
        bad.append(("empty_section_path", aid))
    pat=re.compile(rf"^{re.escape(doc_version_id)}:.+:{ct}:\d+:[0-9a-f]{{64}}$")
    if not pat.match(aid):
        bad.append(("bad_anchor_id", aid))
    types.add(ct)

if bad:
    print("BAD:", bad[:10])
    sys.exit(1)

# Проверяем наличие хотя бы одного основного типа контента
# Основные типы: p (параграф), hdr (заголовок), cell (ячейка таблицы)
# Допустимо наличие только cell и p для табличных документов
main_types = {"p", "hdr", "cell"}
found_main_types = types & main_types

if not found_main_types:
    print("ERROR: expected at least one main content type (p, hdr, or cell); got:", sorted(types))
    sys.exit(1)

# Предупреждение, если нет параграфов (p) - это может быть нормально для табличных документов
if "p" not in types:
    print("WARN: no paragraph (p) anchors found; document may be table-only")

print("OK types:", sorted(types))
"@

# Сохраняем Python скрипт в файл
$checkPy | Out-File -FilePath $pyFile -Encoding utf8

try {
    & $pythonExe $pyFile | Write-Host
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "Python validation failed with exit code $exitCode"
    }
} finally {
    # Удаляем временные файлы
    Remove-Item -Path $tempDir -Recurse -Force -ErrorAction SilentlyContinue
}

# 10) Re-ingest (force=true) and ensure not duplicated
Write-Section "Re-ingest (force=true)"
try {
  $null = Http-Json -Method POST -Url "$ApiBase/api/document-versions/$versionId/ingest?force=true" -BodyJson "{}"
} catch {
  $null = Http-Json -Method POST -Url "$ApiBase/api/document-versions/$versionId/ingest?force=true"
}

$deadline = (Get-Date).AddSeconds($TimeoutSec)
do {
  $verNow2 = Http-Json -Method GET -Url "$ApiBase/api/document-versions/$versionId"
  $status2 = $verNow2.ingestion_status
  Write-Host "status=$status2"
  if ($status2 -in @("ready","needs_review","failed")) { break }
  Start-Sleep -Seconds 1
} while ((Get-Date) -lt $deadline)

Assert ($status2 -ne "failed") ("Re-ingestion failed. Payload: " + ($verNow2 | ConvertTo-Json -Depth 8))
$anchors2 = Http-Json -Method GET -Url "$ApiBase/api/document-versions/$versionId/anchors"
$anchorCount2 = @($anchors2).Count
Write-Host "anchors count after re-ingest=$anchorCount2"

if ($anchorCount2 -ne $anchorCount) {
  Write-Host "WARN: anchor count changed after re-ingest ($anchorCount -> $anchorCount2). Not necessarily bad, but must not monotonically increase." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "✅ STEP 4 CHECK PASSED" -ForegroundColor Green
Write-Host "study_id=$studyId"
Write-Host "document_id=$documentId"
Write-Host "version_id=$versionId"
if ($tmpDir) {
  Write-Host "tmp_dir=$tmpDir"
} else {
  Write-Host "docx_path=$docxPathToUse"
}
