# Data Science China Datasets Spider

Scrapling-based spider for Data Science China Datasets (https://github.com/Data-Science-China/datasets)

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
python spider.py --urls https://github.com/Data-Science-China/datasets
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
