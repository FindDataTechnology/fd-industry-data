# FiveThirtyEight Data Spider

Scrapling-based spider for FiveThirtyEight Data, driven by the GitHub git/trees API
(https://api.github.com/repos/fivethirtyeight/data/git/trees/master) since the
rendered github.com repo file listing moved to React and is no longer scrapable.
Each top-level tree entry (dataset directory or file) becomes one record.

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
python spider.py --urls https://api.github.com/repos/fivethirtyeight/data/git/trees/master
```

Note: api.github.com is not reachable directly from the local network; run the
spider behind the fx egress.

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
