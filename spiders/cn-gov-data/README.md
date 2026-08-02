# 国家数据 Spider

Scrapling-based spider for 国家数据 (https://data.cn.gov.cn/)

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
python spider.py --urls https://data.cn.gov.cn/
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
