# Скрипты для развертывания и работы с ClinNexus

## setup-server.sh

Скрипт автоматической подготовки Ubuntu сервера для развертывания ClinNexus.

### Использование

```bash
# Скачайте скрипт на сервер
wget https://raw.githubusercontent.com/your-repo/clinnexus/main/scripts/setup-server.sh

# Или скопируйте через scp
scp scripts/setup-server.sh user@server:/tmp/

# Запустите с правами root
sudo bash setup-server.sh
```

### Что делает скрипт

1. Обновляет систему
2. Устанавливает Docker и Docker Compose
3. Настраивает firewall (ufw)
4. Создает необходимые директории
5. Настраивает системные лимиты

### Требования

- Ubuntu 20.04 LTS или новее
- Права root (sudo)
- Интернет соединение

### После выполнения скрипта

1. Скопируйте код приложения на сервер
2. Создайте файл `.env.prod` на основе `env.prod.example`
3. Запустите приложение: `docker compose -f docker-compose.prod.yml up -d`

Подробные инструкции см. в [DEPLOYMENT.md](../DEPLOYMENT.md).

---

## batch_upload_ingest.py

Скрипт для массовой загрузки и ингестии документов с поддержкой бенчмарков.

### Описание

Скрипт принимает на вход:
- путь к одному файлу (.docx, .pdf, .xlsx)
- путь к папке с файлами

Для каждого файла:
1. Создаёт study/document/version (если нужно)
2. Загружает файл через upload API
3. Запускает процесс ингестии
4. Ожидает завершения ингестии
5. Сохраняет артефакты бенчмарков (факты и SoA) в `benchmark_results/`

### Использование

```bash
# Обработка одного файла
python scripts/batch_upload_ingest.py --file path/to/document.docx

# Обработка папки с файлами
python scripts/batch_upload_ingest.py --folder path/to/documents/

# С опцией --resume (пропускать уже обработанные файлы по SHA256)
python scripts/batch_upload_ingest.py --folder path/to/documents/ --resume

# С указанием workspace_id и study_code
python scripts/batch_upload_ingest.py --folder path/to/documents/ --workspace-id <uuid> --study-code "STUDY-001"
```

### Параметры

- `--file <path>` — путь к одному файлу для обработки
- `--folder <path>` — путь к папке с файлами
- `--workspace-id <uuid>` — ID рабочего пространства (опционально, по умолчанию используется первый доступный)
- `--study-code <code>` — код исследования (опционально, извлекается из имени файла)
- `--resume` — пропускать уже обработанные файлы (проверка по SHA256 в БД)
- `--api-base <url>` — базовый URL API (по умолчанию `http://localhost:8000`)

### Результаты бенчмарков

После успешной ингестии скрипт автоматически сохраняет артефакты в `benchmark_results/{study_code}/`:
- `v{version_number}_facts.json` — извлечённые факты исследования
- `v{version_number}_soa.json` — извлечённая таблица SoA (Schedule of Activities)

Также создаётся файл `benchmark_summary.csv` в корне проекта с агрегированной статистикой:
- `study_code` — код исследования
- `file_name` — имя файла
- `version` — версия документа
- `status` — статус обработки (ready, needs_review, failed)
- `anchors_count` — количество созданных anchors
- `soa_confidence` — уверенность в извлечении SoA
- `matched_anchors` — количество совпавших anchors (при сравнении версий)
- `changed_anchors` — количество изменённых anchors
- `processing_time_sec` — время обработки в секундах

### Поддерживаемые форматы

- `.docx` — документы Word (приоритетный формат)
- `.pdf` — PDF документы (цифровые, не сканы)
- `.xlsx` — таблицы Excel

**Примечание**: Файлы с расширением `.doc` (старый формат Word) будут пропущены с предупреждением.

### Примеры использования

```bash
# Обработка одного протокола
python scripts/batch_upload_ingest.py \
  --file "protocols/APEIRON APN01-01-COVID19.docx" \
  --study-code "APEIRON APN01-01-COVID19"

# Массовая обработка с возобновлением
python scripts/batch_upload_ingest.py \
  --folder "protocols/" \
  --resume

# Обработка с указанием workspace
python scripts/batch_upload_ingest.py \
  --folder "protocols/" \
  --workspace-id "123e4567-e89b-12d3-a456-426614174000"
```

### Требования

- Python 3.8+
- Установленные зависимости: `requests`, `python-dotenv`
- Запущенный backend API (по умолчанию `http://localhost:8000`)
- Доступ к базе данных для проверки SHA256 (при использовании `--resume`)

### Обработка ошибок

- Скрипт продолжает обработку остальных файлов при ошибке на одном файле
- Ошибки логируются в консоль с указанием файла и причины
- Файлы с ошибками не сохраняются в `benchmark_summary.csv`

