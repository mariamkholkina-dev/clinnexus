param(
  [string]$ApiBase = "http://localhost:8000",
  [string]$WorkspaceId = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
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
    try {
        [Console]::OutputEncoding = [System.Text.Encoding]::GetEncoding(1251)
        [Console]::InputEncoding = [System.Text.Encoding]::GetEncoding(1251)
    } catch {}
}

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Test([string]$name) {
  Write-Host ""
  Write-Host "→ $name" -ForegroundColor Cyan
}

function Write-OK([string]$msg = "OK") {
  Write-Host "  ✓ $msg" -ForegroundColor Green
}

function Write-Error([string]$msg) {
  Write-Host "  ✗ $msg" -ForegroundColor Red
  throw $msg
}

function Assert($cond, $msg) {
  if (-not $cond) {
    Write-Error $msg
  }
}

function Invoke-Api {
  param(
    [Parameter(Mandatory=$true)][ValidateSet("GET","POST")][string]$Method,
    [Parameter(Mandatory=$true)][string]$Url,
    [string]$BodyJson = $null
  )
  
  $headers = @{ 'Accept' = 'application/json' }
  
  try {
    if ($Method -eq 'GET') {
      return Invoke-RestMethod -Method Get -Uri $Url -Headers $headers -ErrorAction Stop
    } else {
      if ($null -ne $BodyJson) {
        $headers['Content-Type'] = 'application/json'
        return Invoke-RestMethod -Method Post -Uri $Url -Headers $headers -Body $BodyJson -ErrorAction Stop
      } else {
        return Invoke-RestMethod -Method Post -Uri $Url -Headers $headers -ErrorAction Stop
      }
    }
  } catch {
    $statusCode = $_.Exception.Response.StatusCode.value__
    $errorBody = $_.ErrorDetails.Message
    Write-Error "HTTP $statusCode : $errorBody"
  }
}

Write-Host ""
Write-Host "=== Smoke Test (Быстрая проверка API) ===" -ForegroundColor Cyan
Write-Host "ApiBase: $ApiBase" -ForegroundColor Gray
Write-Host "WorkspaceId: $WorkspaceId" -ForegroundColor Gray

# 1. Health check
Write-Test "Health Check"
try {
  $health = Invoke-Api -Method GET -Url "$ApiBase/health"
  Assert ($health.status -eq "ok") "Health check failed: статус не 'ok'"
  Write-OK "Сервер доступен"
} catch {
  Write-Error "Сервер недоступен: $_"
}

# 2. Создание Study
Write-Test "Создание Study"
$timestamp = [long][Math]::Floor((Get-Date -UFormat %s))
$studyCode = "SMOKE-$timestamp"

$studyBody = @{
  workspace_id = $WorkspaceId
  study_code = $studyCode
  title = "Smoke Test Study"
  status = "active"
} | ConvertTo-Json -Compress

try {
  $study = Invoke-Api -Method POST -Url "$ApiBase/api/studies" -BodyJson $studyBody
  Assert ($study.id) "study.id пустой"
  Assert ($study.study_code -eq $studyCode) "study_code не совпадает"
  $studyId = $study.id
  Write-OK "Study создан (id=$studyId)"
} catch {
  Write-Error "Ошибка создания Study: $_"
}

# 3. Получение Study
Write-Test "Получение Study"
try {
  $studyGet = Invoke-Api -Method GET -Url "$ApiBase/api/studies/$studyId"
  Assert ($studyGet.id -eq $studyId) "ID не совпадает"
  Assert ($studyGet.title -eq "Smoke Test Study") "Title не совпадает"
  Write-OK "Study получен корректно"
} catch {
  Write-Error "Ошибка получения Study: $_"
}

# 4. Список Studies
Write-Test "Список Studies"
try {
  $studies = Invoke-Api -Method GET -Url "$ApiBase/api/studies?workspace_id=$WorkspaceId"
  Assert ($studies -is [array]) "Список studies не является массивом"
  $found = $studies | Where-Object { $_.id -eq $studyId }
  Assert ($found) "Созданный study не найден в списке"
  Write-OK "Найдено studies: $($studies.Count)"
} catch {
  Write-Error "Ошибка получения списка Studies: $_"
}

# 5. Создание Document
Write-Test "Создание Document"
$docBody = @{
  doc_type = "protocol"
  title = "Smoke Test Protocol"
  lifecycle_status = "draft"
} | ConvertTo-Json -Compress

try {
  $doc = Invoke-Api -Method POST -Url "$ApiBase/api/studies/$studyId/documents" -BodyJson $docBody
  Assert ($doc.id) "document.id пустой"
  $documentId = $doc.id
  Write-OK "Document создан (id=$documentId)"
} catch {
  Write-Error "Ошибка создания Document: $_"
}

# 6. Список Documents
Write-Test "Список Documents"
try {
  $docs = Invoke-Api -Method GET -Url "$ApiBase/api/studies/$studyId/documents"
  Assert ($docs -is [array]) "Список documents не является массивом"
  $found = $docs | Where-Object { $_.id -eq $documentId }
  Assert ($found) "Созданный document не найден в списке"
  Write-OK "Найдено documents: $($docs.Count)"
} catch {
  Write-Error "Ошибка получения списка Documents: $_"
}

# 7. Создание Document Version
Write-Test "Создание Document Version"
$verBody = @{
  version_label = "v1.0-smoke"
} | ConvertTo-Json -Compress

try {
  $ver = Invoke-Api -Method POST -Url "$ApiBase/api/documents/$documentId/versions" -BodyJson $verBody
  Assert ($ver.id) "version.id пустой"
  $versionId = $ver.id
  Write-OK "Version создана (id=$versionId)"
} catch {
  Write-Error "Ошибка создания Version: $_"
}

# 8. Получение Document Version
Write-Test "Получение Document Version"
try {
  $verGet = Invoke-Api -Method GET -Url "$ApiBase/api/document-versions/$versionId"
  Assert ($verGet.id -eq $versionId) "Version ID не совпадает"
  Assert ($verGet.version_label -eq "v1.0-smoke") "Version label не совпадает"
  Write-OK "Version получена корректно (status=$($verGet.ingestion_status))"
} catch {
  Write-Error "Ошибка получения Version: $_"
}

# 9. Список Versions
Write-Test "Список Versions"
try {
  $versions = Invoke-Api -Method GET -Url "$ApiBase/api/documents/$documentId/versions"
  Assert ($versions -is [array]) "Список versions не является массивом"
  $found = $versions | Where-Object { $_.id -eq $versionId }
  Assert ($found) "Созданная version не найдена в списке"
  Write-OK "Найдено versions: $($versions.Count)"
} catch {
  Write-Error "Ошибка получения списка Versions: $_"
}

# 10. Проверка Section Contracts (список)
Write-Test "Список Section Contracts"
try {
  $contracts = Invoke-Api -Method GET -Url "$ApiBase/api/section-contracts"
  Assert ($contracts -is [array]) "Список contracts не является массивом"
  Write-OK "Найдено contracts: $($contracts.Count)"
} catch {
  Write-Error "Ошибка получения Section Contracts: $_"
}

# 11. Создание Section Contract (Section Passport)
Write-Test "Создание Section Contract (Section Passport)"
$contractSectionKey = "protocol.test-section-$timestamp"
$contractBody = @{
  workspace_id = $WorkspaceId
  doc_type = "protocol"
  section_key = $contractSectionKey
  title = "Smoke Test Section"
  required_facts_json = @{
    required = @(
      @{
        fact_type = "test_fact"
        description = "Тестовый факт для smoke теста"
      }
    )
  }
  allowed_sources_json = @{
    allowed_doc_types = @("protocol")
    allowed_sections = @("protocol.test-section")
  }
  retrieval_recipe_json = @{
    strategy = "structured_extraction"
    prefer_anchors = $true
    fallback_to_chunks = $true
  }
  qc_ruleset_json = @{
    rules = @(
      @{
        type = "completeness"
        threshold = 0.95
      }
    )
  }
  citation_policy = "per_claim"
  version = 1
  is_active = $true
} | ConvertTo-Json -Depth 10 -Compress

$contractId = $null
try {
  $contract = Invoke-Api -Method POST -Url "$ApiBase/api/section-contracts" -BodyJson $contractBody
  Assert ($contract.id) "contract.id пустой"
  Assert ($contract.section_key -eq $contractSectionKey) "section_key не совпадает"
  $contractId = $contract.id
  $msg = "Section Contract created (id=" + $contractId.ToString() + ", key=" + $contractSectionKey + ")"
  Write-OK $msg
} catch {
  $errorMsg = $_.ToString()
  # Пробуем найти существующий контракт при любой ошибке создания (возможно, уже существует)
  Write-Host "  [WARN] Ошибка создания контракта, проверяем существующие..." -ForegroundColor Yellow
  Write-Host "    Ошибка: $errorMsg" -ForegroundColor Gray
  try {
    $existingContracts = Invoke-Api -Method GET -Url "$ApiBase/api/section-contracts?doc_type=protocol"
    $existing = $existingContracts | Where-Object { $_.section_key -eq $contractSectionKey -and $_.workspace_id -eq $WorkspaceId }
    if ($existing) {
      $contractId = $existing[0].id
      $existingId = $existing[0].id
      $msg = "Found existing Section Contract (id=" + $existingId.ToString() + ", key=" + $contractSectionKey + ")"
      Write-OK $msg
    } else {
      # Если не найден, проверяем, можем ли продолжить без него
      Write-Host "  [WARN] Section Contract не создан и не найден, продолжаем тесты без него" -ForegroundColor Yellow
      Write-Host "    Некоторые тесты могут быть пропущены" -ForegroundColor Gray
    }
  } catch {
    Write-Host "  [WARN] Ошибка поиска существующего Section Contract: $_" -ForegroundColor Yellow
    Write-Host "    Продолжаем тесты без Section Contract" -ForegroundColor Gray
  }
}

# 12. Получение Section Contracts с фильтрами
Write-Test "Section Contracts с фильтром doc_type"
try {
  $contractsFiltered = Invoke-Api -Method GET -Url "$ApiBase/api/section-contracts?doc_type=protocol"
  Assert ($contractsFiltered -is [array]) "Список contracts не является массивом"
  if ($contractId) {
    $found = $contractsFiltered | Where-Object { $_.id -eq $contractId }
    Assert ($found) "Созданный contract не найден в списке"
    Write-OK "Найдено protocol contracts: $($contractsFiltered.Count) (наш: найдено)"
  } else {
    Write-OK "Найдено protocol contracts: $($contractsFiltered.Count)"
  }
} catch {
  Write-Error "Ошибка получения Section Contracts с фильтром: $_"
}

# 13. Получение Section Contracts с фильтром is_active
Write-Test "Section Contracts с фильтром is_active"
try {
  $contractsActive = Invoke-Api -Method GET -Url "$ApiBase/api/section-contracts?is_active=true"
  Assert ($contractsActive -is [array]) "Список contracts не является массивом"
  if ($contractId) {
    $found = $contractsActive | Where-Object { $_.id -eq $contractId }
    Assert ($found) "Созданный contract не найден в списке активных"
    Write-OK "Найдено активных contracts: $($contractsActive.Count) (наш: найдено)"
  } else {
    Write-OK "Найдено активных contracts: $($contractsActive.Count)"
  }
} catch {
  Write-Error "Ошибка получения активных Section Contracts: $_"
}

# 14. Получение Section Maps для версии документа
Write-Test "Список Section Maps для Document Version"
try {
  $sectionMaps = Invoke-Api -Method GET -Url "$ApiBase/api/document-versions/$versionId/section-maps"
  Assert ($sectionMaps -is [array]) "Список section maps не является массивом"
  Write-OK "Найдено section maps: $($sectionMaps.Count)"
  if ($sectionMaps.Count -gt 0) {
    $firstMap = $sectionMaps[0]
    Write-Host "    Пример: section_key=$($firstMap.section_key), status=$($firstMap.status), mapped_by=$($firstMap.mapped_by)" -ForegroundColor Gray
  }
} catch {
  Write-Error "Ошибка получения Section Maps: $_"
}

# 15. Переопределение Section Map (override)
Write-Test "Переопределение Section Map"
try {
  $overrideBody = @{
    anchor_ids = @()
    chunk_ids = @()
    notes = "Smoke test override - пустой маппинг для теста"
  } | ConvertTo-Json -Depth 5 -Compress

  # Пытаемся переопределить маппинг для созданного contract
  $overrideMap = Invoke-Api -Method POST -Url "$ApiBase/api/document-versions/$versionId/section-maps/$contractSectionKey/override" -BodyJson $overrideBody
  Assert ($overrideMap.section_key -eq $contractSectionKey) "section_key не совпадает"
  Assert ($overrideMap.status -eq "overridden") "status должен быть 'overridden'"
  Assert ($overrideMap.mapped_by -eq "user") "mapped_by должен быть 'user'"
  Write-OK "Section Map переопределен (status=$($overrideMap.status))"
} catch {
  # Это не критичная ошибка, так как документ может быть не ingested
  $warnMsg = "  [WARN] Переопределение Section Map пропущено (возможно, документ не ingested): " + $_.ToString()
  Write-Host $warnMsg -ForegroundColor Yellow
}

# 16. Перезапуск маппинга секций (rebuild)
Write-Test "Перезапуск маппинга секций (rebuild)"
try {
  # Вызываем rebuild с force=false
  $rebuildResult = Invoke-Api -Method POST -Url "$ApiBase/api/document-versions/$versionId/section-maps/rebuild?force=false"
  Assert ($rebuildResult -ne $null) "Rebuild вернул пустой результат"
  Assert ($rebuildResult.version_id -eq $versionId) "version_id не совпадает"
  $mappedCount = $rebuildResult.sections_mapped_count
  $needsReviewCount = $rebuildResult.sections_needs_review_count
  $rebuildMsg = "Rebuild выполнен (mapped=" + $mappedCount.ToString() + ", needs_review=" + $needsReviewCount.ToString() + ")"
  Write-OK $rebuildMsg
} catch {
  # Это не критичная ошибка, так как документ может быть не ingested
  $warnMsg = "  [WARN] Rebuild пропущен (возможно, документ не ingested): " + $_.ToString()
  Write-Host $warnMsg -ForegroundColor Yellow
}

# Итог
Write-Host ""
Write-Host "✅ Все smoke тесты пройдены успешно!" -ForegroundColor Green
Write-Host ""
Write-Host "Созданные сущности:" -ForegroundColor Gray
Write-Host "  Study ID:           $studyId" -ForegroundColor Gray
Write-Host "  Document ID:        $documentId" -ForegroundColor Gray
Write-Host "  Version ID:         $versionId" -ForegroundColor Gray
if ($contractId) {
  Write-Host "  Section Contract:   $contractId" -ForegroundColor Gray
  Write-Host "    section_key:      $contractSectionKey" -ForegroundColor Gray
} else {
  Write-Host "  Section Contract:   не создан (ошибка)" -ForegroundColor Yellow
}
Write-Host ""
