# 🇺🇦 Ukrainian Legal RAG Pipeline

Pipeline для завантаження українського законодавства у форматі Markdown чанків до Cloudflare R2 для використання з AutoRAG та AI/Vector Search.

## 📋 Огляд

Цей проект автоматизує:
1. **Завантаження** законодавства з [data.rada.gov.ua](https://data.rada.gov.ua)
2. **Конвертацію** у чисті Markdown чанки з метаданими
3. **Завантаження** до Cloudflare R2 (S3-сумісне сховище)
4. **Структуровану організацію** документів для RAG-систем

## 🗂️ Джерела даних

### Портал відкритих даних Верховної Ради України

| Набір даних | Опис | Кількість |
|-------------|------|-----------|
| **Первинні законодавчі акти** | Закони, кодекси (крім тих, що вносять зміни) | ~3000+ документів |
| **Конституція України** | Основний Закон з поділом на статті | 161 стаття |
| **Кодекси** | Цивільний, Кримінальний, Податковий та інші | 15+ кодексів |
| **Міжнародні договори** | Ратифіковані Україною договори | ~2000+ документів |

### API Endpoints

- Тексти документів: `https://data.rada.gov.ua/laws/show/{nreg}.txt`
- JSON картки: `https://data.rada.gov.ua/laws/show/{nreg}.json`
- Списки актів: `https://data.rada.gov.ua/ogd/zak/laws/data/csv/perv1.txt`

## 🚀 Швидкий старт

### 1. Встановлення

```bash
# Клонувати репозиторій
cd semantyka-rag-legal-generic-pipeline

# Створити віртуальне середовище
python -m venv venv
source venv/bin/activate  # Linux/Mac
# або: venv\Scripts\activate  # Windows

# Встановити залежності
pip install -r requirements.txt
```

### 2. Налаштування R2

```bash
# Скопіювати шаблон конфігурації
cp .env.example .env

# Редагувати .env та додати свої ключі R2
nano .env
```

Для отримання ключів R2:
1. Увійдіть до [Cloudflare Dashboard](https://dash.cloudflare.com)
2. Перейдіть до R2 → Overview
3. Створіть API Token з правами Read/Write

### 3. Запуск

```bash
# Тестовий запуск (лише Конституція, локальне збереження)
python pipeline.py --test --local

# Повний пайплайн з лімітом
python pipeline.py --limit 100

# Тільки Конституція та Кодекси
python pipeline.py --constitution-only
python pipeline.py --include-codes --limit 0 --threads 8

# Повний пайплайн включно з міжнародними договорами
python pipeline.py --include-international

# Завантажити існуючі локально в R2
python updaload_existing.py --workers 20
```

## 📁 Структура сховища R2

```
ukrainian-legal-docs/
├── constitution/
│   ├── _metadata.json
│   ├── constitution_article_1.md
│   ├── constitution_article_2.md
│   └── ...
├── codes/
│   ├── 435-15/              # Цивільний кодекс
│   │   ├── _metadata.json
│   │   └── *.md
│   ├── 2341-14/             # Кримінальний кодекс
│   └── ...
├── laws/
│   ├── 2939-17/             # Закон про доступ до інформації
│   │   ├── _metadata.json
│   │   └── *.md
│   └── ...
└── _metadata/
    └── document_index.json
```

## 📄 Формат Markdown чанків

Кожен чанк містить YAML frontmatter з метаданими:

```markdown
---
doc_id: 254к_96-вр
chunk_id: constitution_article_1
chunk_index: 1
title: "Конституція України"
section_type: article
section_number: "Стаття 1"
source: data.rada.gov.ua
language: uk
---

## Стаття 1

Україна є суверенна і незалежна, демократична, соціальна, правова держава.
```

## ⚙️ Конфігурація

### Змінні середовища

| Змінна | Опис | Обов'язково |
|--------|------|-------------|
| `R2_ENDPOINT_URL` | URL Cloudflare R2 | Так |
| `R2_ACCESS_KEY_ID` | Ключ доступу R2 | Так |
| `R2_SECRET_ACCESS_KEY` | Секретний ключ R2 | Так |
| `R2_BUCKET_NAME` | Назва bucket | Так |
| `RADA_API_TOKEN` | Токен API Ради | Ні |

### Параметри CLI

```
--constitution-only   Обробити лише Конституцію
--include-codes       Включити основні Кодекси
--include-international  Включити міжнародні договори
--limit N             Обмежити кількість документів
--local               Зберігати локально (без R2)
--output-dir DIR      Директорія для локального збереження
--recent-only N       Обробити лише нещодавні оновлення
--debug               Увімкнути детальне логування
--test                Швидкий тест (лише Конституція)
```

## 🔧 Модулі

| Модуль | Призначення |
|--------|-------------|
| `config.py` | Конфігурація пайплайну |
| `rada_api_client.py` | Клієнт API data.rada.gov.ua |
| `markdown_converter.py` | Конвертація HTML → Markdown |
| `r2_uploader.py` | Завантаження до R2/локально |
| `pipeline.py` | Головний оркестратор |

## 📊 Рейт-ліміти API

API data.rada.gov.ua має обмеження:
- 60 запитів/хвилина
- 100,000 запитів/день
- 200 MB/день

Пайплайн автоматично додає затримку 6 секунд між запитами.

## 🔗 Інтеграція з AutoRAG

Після завантаження до R2, підключіть bucket до AutoRAG:

1. Налаштуйте AutoRAG на читання з вашого R2 bucket
2. Markdown файли автоматично індексуються
3. Метадані у frontmatter використовуються для фільтрації

### Приклад запиту до AutoRAG

```
Які права громадянина гарантує Конституція України?
```

AutoRAG знайде релевантні статті Конституції та надасть відповідь з посиланнями.

## 📈 Статистика

Типовий повний пайплайн:
- **Конституція**: ~161 чанк (по статтях)
- **Кодекси**: ~15,000+ чанків
- **Первинні закони**: ~50,000+ чанків
- **Час обробки**: 8-12 годин (з рейт-лімітами)

## 🤝 Ліцензія

Дані Верховної Ради України доступні під ліцензією [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/deed.uk).

Код проекту: MIT License

## 📞 Контакти

- Портал відкритих даних: [data.rada.gov.ua](https://data.rada.gov.ua)
- Законодавство України: [zakon.rada.gov.ua](https://zakon.rada.gov.ua)
