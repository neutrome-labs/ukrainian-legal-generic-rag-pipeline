# Верховна Рада: інтеграція

Законодавство України з [data.rada.gov.ua](https://data.rada.gov.ua): Конституція, кодекси, первинні закони, за потреби — міжнародні договори. Кожен документ стає одним Markdown-файлом з YAML frontmatter; розбиття на фрагменти виконує Cloudflare AI Search під час індексації.

## Швидкий старт

```bash
cp .env.example .env   # заповніть R2_* змінні

# Тест: лише Конституція, локально без R2
python -m src.cli.rada --test --local

# Повний пайплайн з лімітом; R2
python -m src.cli.rada --limit 100

# Тільки Конституція та Кодекси, 8 потоків
python -m src.cli.rada --include-codes --limit 0 --threads 8

# Включно з міжнародними договорами
python -m src.cli.rada --include-international

# Недавні оновлення (інкрементально), локально
python -m src.cli.sync --pages 1 --local

# Завантажити раніше створені локальні файли в R2
python -m src.cli.upload --input-dir ./cache/edrsr/documents --workers 20
```

## Параметри CLI (`python -m src.cli.rada`)

| Параметр | Дія |
|----------|-----|
| `--test` | Швидкий тест: лише Конституція |
| `--constitution-only` | Лише Конституція |
| `--include-codes` (default: true) | Включити основні кодекси |
| `--include-international` | Включити міжнародні договори |
| `--limit N` | Обмежити кількість первинних актів (`0` = жодного) |
| `--threads N` | Потоки завантаження (default: 4) |
| `--local` / `--output-dir DIR` | Локальне збереження замість R2 |
| `--skip-existing` | Пропустити вже завантажені (resume) |
| `--recent-only PAGES` | Лише нещодавно оновлені документи |
| `--debug` | Детальне логування |

`--recent-only` — синхронізація змін; те саме вміє `python -m src.cli.sync` (є `--schedule` для періодичного запуску).

## Конфігурація

`.env` у корені (або legacy `src/.env`); змінні оточення мають пріоритет.

| Змінна | Призначення |
|--------|-------------|
| `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME` | Cloudflare R2; обов'язково лише для режиму R2 |
| `RADA_API_TOKEN` | Необов'язковий токен JSON API Ради |
| `CACHE_DIR`, `OUTPUT_DIR`, `LOG_LEVEL` | Шляхи кешу/виводу та рівень логів |

## Схема сховища

```text
ukrainian-legal-docs/
├── constitution/254к_96-вр.md
├── codes/435-15.md           # doc_type "code" → codes/
├── laws/{nreg}.md
└── _metadata/
    ├── {doc_type}/{id}.json  # маркер processed + метадані
    └── document_index.json
```

Локальний режим дає тугу саму структуру в `--output-dir`.

## Формат документа

```markdown
---
doc_id: 254к_96-вр
title: "Конституція України"
source: data.rada.gov.ua
language: uk
---

## Стаття 1
...
```

Конвертер розпізнає «Стаття/Розділ/Частина» як заголовки Markdown. Метадані (`nreg`, `dokid`, `status`, дати, типи) пишуться і в frontmatter, і в `_metadata/{doc_type}/{id}.json`. Маркер processed створюється лише після успішного запису самого документа — невдале завантаження не лишає «привидів» і документ можна повторно обробити. Один документ джерела = один Markdown; chunking — на боці AI Search.

## API та обмеження

- Списки актів: `ogd/zak/laws/data/csv/perv1.txt` (первинні), `perv2.txt` (міжнародні), `perv0.txt` (нечинні); CP1251.
- Документ: `laws/show/{nreg}.json` (картка+структура), `laws/show/{nreg}.txt` (текст).
- Ліміти порталу: 60 req/хв, 100 000 req/день; клієнт тримає ~6 c між запитами, retry на 429/5xx.

Типовий повний прогін: ~3000+ первинних актів, 8–12 годин з лімітами. `--skip-existing` робить повторний запуск дешевим.

## Код

| Модуль | Роль |
|--------|------|
| `src/sources/rada/client.py` | API-клієнт, rate limiter, парсинг карток |
| `src/sources/rada/converter.py` | HTML/текст → Markdown зі структурою |
| `src/pipelines/rada.py` | Оркестрація (Конституція → Кодекси → Закони) |
| `src/cli/rada.py`, `sync.py`, `upload.py` | CLI |

## Ліцензія

Дані Верховної Ради України — [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/deed.uk).
