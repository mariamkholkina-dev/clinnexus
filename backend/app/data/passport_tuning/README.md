# Passport Tuning - Разметка кластеров заголовков

## Описание

Веб-интерфейс для ручного сопоставления кластеров заголовков с `section_key`, позволяющий получать файл `cluster_to_section_key.json`.

## Структура файлов

- `clusters.json` - исходные кластеры заголовков (создается оффлайн шагом)
- `cluster_to_section_key.json` - результат маппинга (создается через UI)

## Формат cluster_to_section_key.json

```json
{
  "0": {
    "doc_type": "protocol",
    "section_key": "protocol.endpoints",
    "title_ru": "Конечные точки"
  },
  "1": {
    "doc_type": "csr",
    "section_key": "csr.synopsis",
    "title_ru": "Краткое резюме"
  }
}
```

## Запуск локально

### Backend

1. Убедитесь, что backend запущен:
```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

2. Проверьте, что файл `backend/app/data/passport_tuning/clusters.json` существует
   - Если файла нет, создайте его или скопируйте из `backend/clusters.json` (первые N записей для тестирования)

3. Проверьте настройки путей в `.env` (опционально):
```env
PASSPORT_TUNING_CLUSTERS_PATH=app/data/passport_tuning/clusters.json
PASSPORT_TUNING_MAPPING_PATH=app/data/passport_tuning/cluster_to_section_key.json
```

### Frontend

1. Убедитесь, что frontend запущен:
```bash
cd frontend
npm run dev
```

2. Проверьте, что `frontend/example.env` содержит правильный URL backend:
```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api
```

3. Откройте в браузере:
```
http://localhost:3000/passport-tuning/cluster-mapping
```

## Использование

1. **Выбор кластера**: Кликните на кластер в левой колонке
2. **Просмотр деталей**: В правой колонке отображаются:
   - Топ заголовки (RU и EN)
   - Примеры использования
   - Статистика кластера
3. **Заполнение формы**:
   - `doc_type`: выберите тип документа (protocol, csr, sap, и т.д.)
   - `section_key`: введите ключ секции (автодополнение доступно)
   - `title_ru`: введите русское название секции
4. **Сохранение**:
   - `Save`: сохранить текущий маппинг
   - `Save & Next`: сохранить и перейти к следующему неразмеченному кластеру
   - `Clear`: очистить форму
5. **Экспорт**: Нажмите "Download JSON" для скачивания готового файла

## API Endpoints

### GET `/api/passport-tuning/clusters`
Получение списка кластеров с пагинацией и поиском.

**Параметры:**
- `page` (int, default: 1) - номер страницы
- `page_size` (int, default: 100, max: 1000) - размер страницы
- `search` (string, optional) - поисковый запрос по заголовкам

**Ответ:**
```json
{
  "items": [...],
  "total": 100
}
```

### GET `/api/passport-tuning/mapping`
Получение текущего маппинга.

**Ответ:**
```json
{
  "mapping": {
    "0": {
      "doc_type": "protocol",
      "section_key": "protocol.endpoints",
      "title_ru": "Конечные точки"
    }
  }
}
```

### POST `/api/passport-tuning/mapping`
Сохранение полного маппинга.

**Тело запроса:**
```json
{
  "0": {
    "doc_type": "protocol",
    "section_key": "protocol.endpoints",
    "title_ru": "Конечные точки"
  }
}
```

**Ответ:**
```json
{
  "message": "Mapping успешно сохранен",
  "items_count": 1
}
```

### GET `/api/passport-tuning/mapping/download`
Скачивание готового JSON файла как attachment.

## Валидация

Backend валидирует:
- `cluster_id`: строка (преобразуется из любого типа)
- `doc_type`: enum (protocol, csr, sap, tfl, ib, icf, other)
- `section_key`: непустая строка
- `title_ru`: непустая строка
- Отсутствие лишних полей в запросе

Frontend показывает предупреждения:
- Если `title_ru` пустой
- Если `section_key` не начинается с `doc_type.`

## Защита от гонок

Backend использует `threading.Lock` для защиты от одновременной записи в файл маппинга. Запись выполняется атомарно через временный файл.

