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
# Законодавство Ради: тестовий запуск (Конституція, локально)
python -m src.cli.rada --test --local

# Судові рішення: завантажити річний експорт і зробити preview
python -m src.cli.edrsr_datasets --years 2025 --download
python -m src.cli.edrsr 2025 --preview --limit 10
```

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
