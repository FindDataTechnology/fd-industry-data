# API开放平台 Spider

Scrapling-based spider for API开放平台 (https://open.apipoer.com/)

## Installation

```bash
pip install scrapling
```

## Usage

### Basic Usage

```bash
python spider.py
```

### Custom URLs

```bash
python spider.py --urls https://open.apipoer.com/
```

### Dry Run (No Save)

```bash
python spider.py --dry-run
```

## Output

- **SQLite**: `data/data.sqlite`
- **JSON Lines**: `output/export.jsonl`

## Features

- 5 rotating user agents
- 3 retries per request
- 2 second request delay
- Automatic SQLite and JSON export
- Command-line argument support
