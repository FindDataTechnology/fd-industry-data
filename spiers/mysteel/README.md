# MySteel Spider - 我的钢铁网爬虫

## Overview

Scrapling-based spider for extracting steel industry data from MySteel (我的钢铁网).

## Data Sources

**Target URL**: https://www.mysteel.com

### Publicly Accessible Data
- Steel price index (MSPI) daily
- Major product prices (rebar, HRC, CRC, wire rod, plate)
- Steel industry news headlines
- Production overview summaries
- Iron ore port inventory snapshots

### Premium Content (Membership Required)
- Detailed daily prices by region/grade/specification
- Historical price database (20+ years)
- Production statistics by mill
- Import/export detailed customs data
- Downstream demand indicators
- Cost model and margin analysis

## Authentication Requirements

**MySteel Membership**:
- Gold membership: ~¥10,000-30,000/year
- Silver membership: ~¥5,000-10,000/year
- API access requires separate data API license

**Public Website**:
- No authentication needed for price indices and basic news
- Limited price data (major cities only)
- Suitable for price trend monitoring

## Installation

```bash
cd fd-industry-data
uv sync
```

## Usage

### Command Line

```bash
cd spiers/mysteel
python spider.py
```

### Python API

```python
from mysteel.spider import get_mysteel_data

# Fetch all categories
results = get_mysteel_data()

# Fetch specific categories
results = get_mysteel_data(categories=["prices", "news"])

# Access results
prices = results["steel_prices"]
index = results["steel_index"]
news = results["steel_news"]
production = results["production_stats"]
raw_materials = results["raw_material_prices"]
```

## Data Categories

### 1. Steel Prices (steel_prices)
- **Sources**: Price pages for rebar, HRC, CRC, wire rod, plate
- **Fields**: product, product_cn, specification, price_low, price_high, price_avg, price_change, price_change_pct, unit, region, city, trade_date
- **Frequency**: Daily
- **Coverage**: Major cities (Shanghai, Beijing, Guangzhou, etc.)

### 2. Steel Index (steel_index)
- **Sources**: MSPI index, steel index
- **Fields**: index_name, index_value, change_value, change_pct, trade_date
- **Frequency**: Daily

### 3. Steel News (steel_news)
- **Sources**: Steel news, market news, international news
- **Fields**: title, summary, category, source, publish_date, source_url
- **Frequency**: Real-time

### 4. Production Stats (production_stats)
- **Sources**: Production data, inventory data
- **Fields**: product, production_volume, capacity_utilization, inventory_volume, unit, stat_period, yoy_change, mom_change
- **Frequency**: Weekly/Monthly

### 5. Raw Material Prices (raw_material_prices)
- **Sources**: Iron ore, coke prices
- **Fields**: material, price, price_change, price_change_pct, unit, specification, trade_date
- **Frequency**: Daily

## Steel Products Covered

| Product | Chinese Name | Specification | Unit |
|---------|--------------|---------------|------|
| Rebar | 螺纹钢 | HRB400 20mm | 元/吨 |
| HRC | 热轧板卷 | Q235B 4.75mm | 元/吨 |
| CRC | 冷轧板卷 | SPCC 1.0mm | 元/吨 |
| Wire Rod | 线材 | HPB300 6.5mm | 元/吨 |
| Plate | 中厚板 | Q235B 20mm | 元/吨 |
| Angle Steel | 角钢 | Q235B 5# | 元/吨 |
| H-Beam | H型钢 | Q235B 200*200 | 元/吨 |

## Output

### SQLite Database
Location: `data/mysteel.db`

Tables:
- `steel_prices`
- `steel_index`
- `steel_news`
- `production_stats`
- `raw_material_prices`

### JSON Export
Location: `output/mysteel_data.json`

Contains all extracted data in JSON format.

## Anti-Bot Measures

This spider implements:
- Browser impersonation (Chrome)
- Stealthy headers
- Rate limiting (3s delay between requests)
- Proper User-Agent rotation

## Rate Limiting

- **Delay**: 3 seconds between requests
- **Concurrent Requests**: 1 (sequential)
- **Timeout**: 30 seconds per request

## Known Limitations

1. **Membership Wall**: Detailed prices require MySteel membership
2. **Regional Coverage**: Public data limited to major cities
3. **Historical Data**: Limited historical coverage without subscription
4. **JavaScript Rendering**: Some pages may require browser automation

## Troubleshooting

### No data extracted
- Check if MySteel website is accessible
- Verify HTML structure hasn't changed
- Check logs for HTTP errors

### HTTP 403/429 errors
- Increase delay between requests
- Check if IP is blocked
- Consider using proxy rotation

### Missing regional data
- Public pages only show major cities
- Detailed regional data requires membership
- Check source HTML for available cities

## Data Quality

| Data Type | Reliability | Timeliness | Completeness |
|-----------|-------------|------------|--------------|
| Steel Prices | ⭐⭐⭐⭐⭐ | Daily | Medium (public) |
| Steel Index | ⭐⭐⭐⭐⭐ | Daily | High |
| News | ⭐⭐⭐⭐⭐ | Real-time | High |
| Production Stats | ⭐⭐⭐⭐ | Weekly/Monthly | Medium |
| Raw Materials | ⭐⭐⭐⭐⭐ | Daily | Medium |

## Comparison with Alternatives

| Feature | MySteel (Public) | MySteel (Member) | SMM | SteelHome |
|---------|------------------|------------------|-----|-----------|
| Cost | Free | ¥¥ | Free | Free |
| Steel Prices | Limited | Full | ✅ | ✅ |
| Price Index | ✅ | ✅ | ✅ | ✅ |
| Production Data | ❌ | ✅ | ❌ | ❌ |
| Historical Data | ❌ | ✅ | ❌ | ❌ |
| News | ✅ | ✅ | ✅ | ✅ |

**Recommendation**: MySteel is the **most authoritative source** for Chinese steel market data. Public data is sufficient for price trend monitoring.

## Recommended Use Cases

✅ **Good for**:
- Steel price monitoring
- Market trend analysis
- Industry news tracking
- Production capacity analysis
- Raw material cost tracking

❌ **Not suitable for**:
- Detailed regional price analysis (requires membership)
- Long-term historical research (requires membership)
- Mill-level production data (requires membership)

## Steel Industry Coverage

### Upstream (Raw Materials)
- Iron ore (铁矿石)
- Coking coal (焦煤)
- Coke (焦炭)
- Scrap steel (废钢)
- Ferroalloys (铁合金)

### Midstream (Steel Products)
- Long products (长材): Rebar, wire rod, sections
- Flat products (板材): HRC, CRC, plate, coated steel
- Tubular products (管材): Seamless steel pipe, welded pipe
- Stainless steel (不锈钢)

### Downstream (End Use)
- Construction (建筑)
- Automotive (汽车)
- Home appliances (家电)
- Machinery (机械)
- Shipbuilding (船舶)

## License

Data sourced from MySteel (我的钢铁网/Mysteel). For commercial use, verify licensing terms with Mysteel.

## See Also

- [SMM Metals Spider](../smm-metals/) - Non-ferrous metal prices
- [Eastmoney Choice Spider](../eastmoney-choice/) - Financial data
- [Wind Financial Spider](../wind-financial/) - Premium financial terminal

---

**Last Updated**: 2026-07-31  
**Status**: ✅ Ready  
**Access**: Public (limited) / Premium (full)
