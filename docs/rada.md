# Верховна Рада: інтеграція

Законодавство України з [data.rada.gov.ua](https://data.rada.gov.ua): Конституція, кодекси, первинні закони, за потреби — міжнародні договори. Кожен документ стає одним Markdown-файлом з YAML frontmatter; розбиття на фрагменти виконує Cloudflare AI Search під час індексації.

## Швидкий старт

```bash
cp .env.example .env   # заповніть R2_* змінні для віддаленого запису

# Лише Конституція, локально без R2
python -m src.cli.rada --local --no-include-codes --no-include-laws

# План повного запуску: без мережі та записів
python -m src.cli.rada --dry-run

# Повний пайплайн з лімітом; R2
python -m src.cli.rada --remote --limit 100

# Тільки Конституція та Кодекси, 8 працівників
python -m src.cli.rada --no-include-laws --workers 8

# Міжнародні договори незалежні від первинних внутрішніх актів
python -m src.cli.rada --no-include-laws --include-international

# Недавні оновлення (інкрементально), локально
python -m src.cli.rada --recent --pages 2 --local
python -m src.cli.sync --pages 1 --local
python -m src.cli.sync --schedule --interval 6 --remote

# Перелік локальних файлів без доступу до R2, потім завантаження
python -m src.cli.upload --input-dir ./cache/edrsr/documents --dry-run
python -m src.cli.upload --input-dir ./cache/edrsr/documents --remote --workers 20
```

## Спільні правила CLI

Булеві параметри використовують `BooleanOptionalAction`: `--foo` вмикає, `--no-foo` вимикає; значення `true`/`false` після прапорця не передаються. Виняток — вибір сховища: `--local` і `--remote` взаємовиключні; `--no-local` та `--no-remote` не існують. `--output-dir` сам по собі не перемикає сховище.

`rada` та `sync` за замовчуванням пишуть у R2 (`--remote`). `upload` підтримує лише R2: приймає `--remote`, але не `--local`. У всіх команд `--dry-run` / `--no-dry-run` має default `false`. Для `rada` та `sync` dry-run лише друкує план: без мережі, створення кешу, лог-файлів чи записів у сховище; навіть із `--schedule` цикл не запускається. Для `upload` dry-run перелічує локальні файли без доступу до R2, тому не перевіряє, які ключі вже існують.

## Параметри CLI (`python -m src.cli.rada`)

| Параметр | За замовчуванням | Дія |
|----------|-----------------|-----|
| `-h`, `--help` | — | Довідка й вихід |
| `--include-constitution` / `--no-include-constitution` | `true` | Окремий етап Конституції |
| `--include-codes` / `--no-include-codes` | `true` | Окремий етап основних кодексів |
| `--include-laws` / `--no-include-laws` | `true` | Етап первинних внутрішніх актів |
| `--include-international` / `--no-include-international` | `false` | Міжнародні договори; незалежно від `--include-laws` |
| `--limit N` | без ліміту | Максимум первинних актів (внутрішніх і міжнародних); `0` вимикає цей етап |
| `--workers N` | `4` | Кількість паралельних працівників завантаження |
| `--local` / `--remote` | `--remote` | Взаємовиключний вибір сховища |
| `--output-dir DIR` | `OUTPUT_DIR` або `./output` | Корінь локального виводу |
| `--skip-existing` / `--no-skip-existing` | `false` | Пропуск уже збережених документів (resume) |
| `--active-only` / `--no-active-only` | `true` | Фільтрування нечинних актів |
| `--generate-index` / `--no-generate-index` | `true` | Генерація індексу після повного запуску |
| `--recent` / `--no-recent` | `false` | Нещодавні оновлення замість повного пайплайна |
| `--pages N` | `1` | Кількість сторінок для `--recent` |
| `--debug` / `--no-debug` | `false` | Детальне логування |
| `--dry-run` / `--no-dry-run` | `false` | Лише план, без мережі та записів |

`--recent` обходить усі перемикачі `--include-*` повного пайплайна та `--generate-index`; кількість сторінок задає `--pages`. Таку синхронізацію також виконує `src.cli.sync`. Перемикач `--no-include-codes` вимикає окремий етап кодексів і виключає налаштований список основних кодексів зі списку первинних актів. Для лише Конституції вимкніть і кодекси, і закони; міжнародні договори за замовчуванням вимкнені.

## Параметри синхронізації (`python -m src.cli.sync`)

| Параметр | За замовчуванням | Дія |
|----------|-----------------|-----|
| `-h`, `--help` | — | Довідка й вихід |
| `--pages N` | `1` | Кількість сторінок недавніх оновлень |
| `--local` / `--remote` | `--remote` | Взаємовиключний вибір сховища |
| `--output-dir DIR` | `./output` | Корінь локального виводу |
| `--schedule` / `--no-schedule` | `false` | Повторювати синхронізацію за розкладом |
| `--interval N` | `6` | Інтервал між запусками в годинах із `--schedule` |
| `--skip-existing` / `--no-skip-existing` | `false` | Пропуск збережених документів; може пропустити нові редакції |
| `--dry-run` / `--no-dry-run` | `false` | Лише план, без мережі та записів |

## Параметри завантаження (`python -m src.cli.upload`)

| Параметр | За замовчуванням | Дія |
|----------|-----------------|-----|
| `-h`, `--help` | — | Довідка й вихід |
| `--input-dir DIR` | `./output` | Локальний каталог файлів для рекурсивного завантаження |
| `--workers N` | `10` | Кількість паралельних працівників |
| `--remote` | R2 | Явний вибір єдиного підтримуваного сховища; `--local` не підтримується |
| `--skip-existing` / `--no-skip-existing` | `true` | Пропуск ключів, які вже є в R2; вимкнення дозволяє їх перезапис |
| `--dry-run` / `--no-dry-run` | `false` | Перелік локальних файлів без доступу до R2 та записів |

## Міграція CLI

Старі назви вилучено без сумісних псевдонімів. Оновіть скрипти:

| Раніше | Тепер |
|--------|-------|
| `rada --test` або `rada --constitution-only` | `rada --include-constitution --no-include-codes --no-include-laws` (міжнародні договори за замовчуванням вимкнені) |
| `rada --threads 8` | `rada --workers 8` |
| `rada --recent-only 2` | `rada --recent --pages 2` |
| `rada --include-codes --limit 0` для Конституції та кодексів | `rada --no-include-laws` (за стандартних інших перемикачів) |
| Булевий прапорець лише для ввімкнення | Парні `--foo` / `--no-foo` |

Для явного перезапису наявних ключів через `upload` використовуйте `--no-skip-existing`: цей параметр тепер дійсно керує пропуском, default лишається `true`. Для `rada` та `sync` default `--skip-existing` — `false`.

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

Локальний режим дає ту саму структуру в `--output-dir`.

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
