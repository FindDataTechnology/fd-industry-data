"""Datasource manifest catalog for fd-industry-data."""

from typing import Any

CATALOG: dict[str, Any] = {
    "version": "1",
    "name": "fd-industry-data",
    "label": "Industry data sources (NBS GDP, commodity futures, etc.)",
    "source_url": "https://github.com/FindDataOfficial/fd-industry-data",
    "scanner_mode": "upstream-curated",
    "requires": [],
    "ranking_seed": [0.5, 0.5],
    "functions": [
        {
            "command": "get_gdp_quarterly",
            "category": "macroeconomics",
            "description": "Fetch quarterly Gross Domestic Product data from China's National Bureau of Statistics (NBS). Returns nominal GDP values in billion CNY.",
            "parameters": [
                {
                    "name": "start_year",
                    "type": "int",
                    "required": False,
                    "description": "Earliest year to include (default: 2010)",
                }
            ],
            "columns": [
                {
                    "name": "period",
                    "type": "str",
                    "description": "Period identifier (e.g., '2020Q1', '2021Q2')",
                    "frequency": "quarterly",
                },
                {
                    "name": "value",
                    "type": "float",
                    "description": "GDP value at current prices",
                    "meaning": "Nominal gross domestic product amount",
                    "frequency": "quarterly",
                },
                {
                    "name": "indicator_code",
                    "type": "str",
                    "description": "Indicator code from NBS system",
                },
                {
                    "name": "indicator_name",
                    "type": "str",
                    "description": "Human-readable indicator name",
                },
                {
                    "name": "unit",
                    "type": "str",
                    "description": "Unit of measurement",
                    "datasource": "NBS",
                },
            ],
            "frequency": "quarterly",
            "verified": True,
        },
        {
            "command": "get_cpi_monthly",
            "category": "macroeconomics",
            "description": "Fetch monthly Consumer Price Index data from China's National Bureau of Statistics (NBS). Measures inflation by tracking price changes of consumer goods and services.",
            "parameters": [
                {
                    "name": "start_year",
                    "type": "int",
                    "required": False,
                    "description": "Earliest year to include (default: 2010)",
                }
            ],
            "columns": [
                {
                    "name": "period",
                    "type": "str",
                    "description": "Period identifier (e.g., '2020-01', '2020-02')",
                    "frequency": "monthly",
                },
                {
                    "name": "value",
                    "type": "float",
                    "description": "CPI index value",
                    "meaning": "Consumer price level relative to base period",
                    "frequency": "monthly",
                },
                {
                    "name": "indicator_code",
                    "type": "str",
                    "description": "Indicator code from NBS system",
                },
                {
                    "name": "indicator_name",
                    "type": "str",
                    "description": "Human-readable indicator name",
                },
                {
                    "name": "unit",
                    "type": "str",
                    "description": "Unit of measurement (% or index points)",
                    "datasource": "NBS",
                },
            ],
            "frequency": "monthly",
            "verified": True,
        },
        {
            "command": "get_ppi_monthly",
            "category": "macroeconomics",
            "description": "Fetch monthly Producer Price Index data from China's National Bureau of Statistics (NBS). Tracks wholesale price changes across industrial sectors.",
            "parameters": [
                {
                    "name": "start_year",
                    "type": "int",
                    "required": False,
                    "description": "Earliest year to include (default: 2010)",
                }
            ],
            "columns": [
                {
                    "name": "period",
                    "type": "str",
                    "description": "Period identifier (e.g., '2020-01', '2020-02')",
                    "frequency": "monthly",
                },
                {
                    "name": "value",
                    "type": "float",
                    "description": "PPI index value",
                    "meaning": "Producer/wholesale price level relative to base period",
                    "frequency": "monthly",
                },
                {
                    "name": "indicator_code",
                    "type": "str",
                    "description": "Indicator code from NBS system",
                },
                {
                    "name": "indicator_name",
                    "type": "str",
                    "description": "Human-readable indicator name",
                },
                {
                    "name": "unit",
                    "type": "str",
                    "description": "Unit of measurement (% or index points)",
                    "datasource": "NBS",
                },
            ],
            "frequency": "monthly",
            "verified": True,
        },
    ],
    "concepts": [
        {
            "column": "value",
            "concept": "gdp.nominal",
            "entity_type": "country",
            "measure": "value",
            "unit": "100M_CNY",
            "frequency": "quarterly",
            "confidence": 0.95,
        },
        {
            "column": "value",
            "concept": "cpi.index",
            "entity_type": "country",
            "measure": "index",
            "unit": "percent",
            "frequency": "monthly",
            "confidence": 0.95,
        },
        {
            "column": "value",
            "concept": "ppi.index",
            "entity_type": "country",
            "measure": "index",
            "unit": "percent",
            "frequency": "monthly",
            "confidence": 0.95,
        },
    ],
    "entities": [
        {
            "entity_type": "country",
            "coverage": "explicit",
            "codes": ["CN"],
        },
    ],
    "fetch": {
        "module": "fd_industry_data.provider:IndustryDataProvider",
    },
}
