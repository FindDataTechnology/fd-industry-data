# GitHub China Data Hub Spider

Scrapling-based spider for GitHub China Data Hub (https://github.com/opendata/china-data-hub)

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
python spider.py --urls https://github.com/opendata/china-data-hub
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
