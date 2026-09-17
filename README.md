# 🇺🇦 Ukrainian Legal RAG Pipeline

Завантажує українські правові документи як повні Markdown-файли з YAML-метаданими до Cloudflare R2 (або локального кешу) для індексації в Cloudflare AI Search.

## Джерела

| Джерело | Що дає | Документація |
|---------|--------|--------------|
| [Верховна Рада](https://data.rada.gov.ua) | Конституція, кодекси, закони, міжнародні договори | [docs/rada.md](docs/rada.md) |
| [ЄДРСР](https://reyestr.court.gov.ua/) через [відкриті дані ДСА](https://data.gov.ua) | Судові рішення (річні експорти 2006–2026) | [docs/edrsr-integration.md](docs/edrsr-integration.md) |

## Швидкий старт

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # заповніть ключі R2 (див. .env.example)
```

```bash
# Законодавство Ради: лише Конституція, локально
python -m src.cli.rada --local --no-include-codes --no-include-laws

# План Ради без мережі та записів
python -m src.cli.rada --dry-run

# Судові рішення: знайти експорт без завантаження архіву
python -m src.cli.edrsr_datasets --years 2025 --download --dry-run

# Завантажити річний експорт і переглянути лише метадані рішень
python -m src.cli.edrsr_datasets --years 2025 --download
python -m src.cli.edrsr 2025 --dry-run --limit 10

# Імпортувати 10 вхідних рядків локально (типове сховище ЄДРСР)
python -m src.cli.edrsr 2025 --local --limit 10
```

## Спільні правила CLI

- Булеві параметри мають парні форми `--foo` / `--no-foo` (`BooleanOptionalAction`), без значень `true`/`false` після прапорця.
- `--local` / `--remote` — взаємовиключний вибір сховища, а не незалежні булеві прапорці. Форм `--no-local` / `--no-remote` немає. Шляхи `--output-dir` / `--cache-dir` самі не змінюють сховище.
- У всіх п'яти команд `--dry-run` / `--no-dry-run` за замовчуванням `false`; звичайний запуск виконує операцію.

| Команда (`python -m src.cli.…`) | Типове сховище | Що робить `--dry-run` |
|--------------------------------|----------------|-----------------------|
| `rada` | R2 (`--remote`); доступний `--local` | Друкує план без мережі та записів |
| `sync` | R2 (`--remote`); доступний `--local` | Друкує план без мережі та записів; не запускає розклад |
| `upload` | Лише R2; приймає `--remote`, не підтримує `--local` | Перелічує локальні файли без доступу до R2, зокрема без перевірки існуючих ключів |
| `edrsr` | Локальне (`--local`); доступний `--remote` | Читає локальні таблиці, друкує лише метадані (типово 10 рядків), без текстів, записів чи R2 |
| `edrsr_datasets` | Лише локальні експорти; перемикачів сховища немає | Шукає та перелічує набори через мережевий CKAN, але не завантажує й не розпаковує архіви |

Для `edrsr_datasets --download` обов'язково вкажіть `--years YEAR [...]` або `--all-years`, навіть у dry-run. Наявні директорії експорту **завжди зберігаються**; перемикача перезапису немає.

Повні таблиці аргументів і defaults: [Рада, sync, upload](docs/rada.md) та [ЄДРСР і набори](docs/edrsr-integration.md). Довідка будь-якої команди: `python -m src.cli.<команда> --help`.

```bash
# Конституція та кодекси; явний R2, 8 працівників
python -m src.cli.rada --remote --no-include-laws --workers 8

# Недавні оновлення обходять --include-* та генерацію індексу
python -m src.cli.rada --recent --pages 2 --local
python -m src.cli.sync --schedule --interval 6 --pages 1 --remote

# Перевірка локальних файлів, потім R2 (пропуск існуючих типово ввімкнено)
python -m src.cli.upload --input-dir ./cache/edrsr/documents --dry-run
python -m src.cli.upload --input-dir ./cache/edrsr/documents --remote
```

## Міграція CLI

Старі назви не мають сумісних псевдонімів — оновіть команди та скрипти.

| Раніше | Тепер |
|--------|-------|
| `rada --test` / `rada --constitution-only` | `rada --include-constitution --no-include-codes --no-include-laws` (міжнародні договори типово вимкнені) |
| `rada --threads N` | `rada --workers N` (default `4`) |
| `rada --recent-only N` | `rada --recent --pages N` (`--recent` типово `false`, `--pages` — `1`) |
| `edrsr --r2` | `edrsr --remote` |
| `edrsr --preview` | `edrsr --dry-run` |
| Булевий прапорець лише для ввімкнення | Парні `--foo` / `--no-foo` |

У `rada` Конституція, кодекси та закони типово ввімкнені, міжнародні договори — вимкнені й незалежні від `--include-laws`. `--active-only` і `--generate-index` типово `true`. `--skip-existing` типово `false` для `rada`, `sync`, `edrsr`, але `true` для `upload` (тепер реально керує пропуском; `--no-skip-existing` дозволяє перезапис ключів). Типові сховища не змінилися.

## Структура

```text
src/
├── core/          # LegalDocument та контракт DocumentSource
├── sources/       # rada (законодавство), edrsr (судова практика)
├── pipelines/     # оркестрація та спільний імпорт-раннер
├── storage/       # локальний та R2 адаптери (namespaces на джерело)
└── cli/           # rada, sync, upload, edrsr, edrsr_datasets
docs/              # інтеграції та оцінка джерел
tests/             # офлайн-тести
```

Нове джерело реалізує `DocumentSource` і повертає `LegalDocument`;
деталі — у [docs/edrsr-integration.md](docs/edrsr-integration.md).

## Тести

```bash
python -m pytest -q
```

## Ліцензія

Дані: CC BY 4.0 (Верховна Рада України, ДСА України). Код: MIT.
