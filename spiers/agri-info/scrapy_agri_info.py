"""
Scrapyling Spider for China Agricultural Information Center (CAISI)
Source: http://www.caisi.org.cn/
Score: 90

Data Types:
- 农业政策文件 (Agricultural policies)
- 农产品价格信息 (Agricultural product prices)
- 生产统计公报 (Production statistics)
- 市场预警报告 (Market warning reports)
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
import re
import json


@dataclass
class PolicyDocument:
    """Policy document structure"""
    title: str
    publish_date: str
    department: str
    doc_number: str
    category: str
    url: str
    summary: str
    content: str
    crawl_date: str
    
    @classmethod
    def from_html(cls, html_data: Dict[str, Any]) -> "PolicyDocument":
        return cls(
            title=html_data.get("title", ""),
            publish_date=html_data.get("publish_date", ""),
            department=html_data.get("department", ""),
            doc_number=html_data.get("doc_number", ""),
            category=html_data.get("category", ""),
            url=html_data.get("url", ""),
            summary=html_data.get("summary", ""),
            content=html_data.get("content", ""),
            crawl_date=datetime.now().isoformat()
        )


@dataclass
class MarketPrice:
    """Market price data structure"""
    product_name: str
    product_code: str
    region: str
    price_unit: str
    current_price: float
    prev_price: float
    change_rate: float
    trade_date: str
    source: str
    url: str
    crawl_date: str
    
    @classmethod
    def from_html(cls, html_data: Dict[str, Any]) -> "MarketPrice":
        return cls(
            product_name=html_data.get("product_name", ""),
            product_code=html_data.get("product_code", ""),
            region=html_data.get("region", ""),
            price_unit=html_data.get("price_unit", ""),
            current_price=float(html_data.get("current_price", 0)),
            prev_price=float(html_data.get("prev_price", 0)),
            change_rate=float(html_data.get("change_rate", 0)),
            trade_date=html_data.get("trade_date", ""),
            source=html_data.get("source", ""),
            url=html_data.get("url", ""),
            crawl_date=datetime.now().isoformat()
        )


@dataclass
class ProductionStats:
    """Production statistics structure"""
    year: int
    quarter: Optional[int]
    province: str
    city: Optional[str]
    category: str
    indicator: str
    value: float
    unit: str
    period: str
    source: str
    url: str
    crawl_date: str
    
    @classmethod
    def from_html(cls, html_data: Dict[str, Any]) -> "ProductionStats":
        return cls(
            year=int(html_data.get("year", 0)),
            quarter=int(html_data.get("quarter", 0)) if html_data.get("quarter") else None,
            province=html_data.get("province", ""),
            city=html_data.get("city"),
            category=html_data.get("category", ""),
            indicator=html_data.get("indicator", ""),
            value=float(html_data.get("value", 0)),
            unit=html_data.get("unit", ""),
            period=html_data.get("period", ""),
            source=html_data.get("source", ""),
            url=html_data.get("url", ""),
            crawl_date=datetime.now().isoformat()
        )


class CAISIDataExtractor:
    """Extract structured data from CAISI HTML content"""
    
    def __init__(self):
        self.base_url = "http://www.caisi.org.cn"
        
    def extract_policy_documents(self, html_content: str) -> List[PolicyDocument]:
        """Extract policy documents from HTML"""
        documents = []
        
        try:
            pattern = r'<div[^>]*class="[^"]*list-item[^"]*"[^>]*>(.*?)</div>'
            matches = re.findall(pattern, html_content, re.DOTALL)
            
            for match in matches:
                title_match = re.search(r'<h3[^>]*>(.*?)</h3>', match, re.DOTALL)
                date_match = re.search(r'<span[^>]*class="date"(.*?)</span>', match, re.DOTALL)
                link_match = re.search(r'<a[^>]*href="([^"]*)"', match)
                
                if title_match and link_match:
                    doc = {
                        "title": re.sub(r'<[^>]+>', '', title_match.group(1)).strip(),
                        "publish_date": re.sub(r'<[^>]+>', '', date_match.group(1)).strip() if date_match else "",
                        "department": "",
                        "doc_number": "",
                        "category": "policy",
                        "url": f"{self.base_url}{link_match.group(1)}" if link_match.group(1).startswith('/') else link_match.group(1),
                        "summary": "",
                        "content": "",
                        "crawl_date": datetime.now().isoformat()
                    }
                    documents.append(PolicyDocument.from_html(doc))
                    
        except Exception as e:
            print(f"Error extracting policy documents: {e}")
            
        return documents
    
    def extract_market_prices(self, html_content: str) -> List[MarketPrice]:
        """Extract market prices from HTML tables"""
        prices = []
        
        try:
            table_pattern = r'<table[^>]*>(.*?)</table>'
            tables = re.findall(table_pattern, html_content, re.DOTALL)
            
            for table in tables:
                rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table, re.DOTALL)
                
                if len(rows) >= 2:
                    header_row = re.sub(r'<[^>]+>', '', rows[0]).split('|')
                    
                    for row in rows[1:4]:
                        cells = re.sub(r'<[^>]+>', '', row).split('|')
                        
                        if len(cells) >= 5:
                            price_data = {
                                "product_name": cells[0].strip() if cells[0] else "",
                                "product_code": cells[1].strip() if cells[1] else "",
                                "region": cells[2].strip() if cells[2] else "",
                                "price_unit": cells[3].strip() if cells[3] else "元/公斤",
                                "current_price": float(cells[4].replace(',', '').replace('元', '')) if cells[4] else 0,
                                "prev_price": 0,
                                "change_rate": 0,
                                "trade_date": "",
                                "source": "CAISI",
                                "url": "",
                                "crawl_date": datetime.now().isoformat()
                            }
                            prices.append(MarketPrice.from_html(price_data))
                            
        except Exception as e:
            print(f"Error extracting market prices: {e}")
            
        return prices
    
    def extract_production_stats(self, html_content: str) -> List[ProductionStats]:
        """Extract production statistics from HTML tables"""
        stats = []
        
        try:
            # Look for statistical tables
            table_pattern = r'<table[^>]*class="[^"]*stats[^"]*"[^>]*>(.*?)</table>'
            tables = re.findall(table_pattern, html_content, re.DOTALL)
            
            for table in tables:
                rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table, re.DOTALL)
                
                if len(rows) >= 2:
                    headers = [re.sub(r'<[^>]+>', '', cell).strip() for cell in re.findall(r'<th[^>]*>(.*?)</th>', table, re.DOTALL)]
                    
                    for row in rows[1:10]:
                        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
                        cells = [re.sub(r'<[^>]+>', '', cell).strip() for cell in cells]
                        
                        if len(cells) >= 5:
                            stat_data = {
                                "year": int(re.search(r'\d{4}', cells[0]).group()) if any(re.search(r'\d{4}', c) for c in cells[:2]) else datetime.now().year,
                                "quarter": None,
                                "province": cells[0] if cells[0] else "",
                                "city": None,
                                "category": "agricultural",
                                "indicator": cells[1] if cells[1] else "",
                                "value": float(cells[-2].replace(',', '').replace('%', '')) if cells[-2] and cells[-2].replace('.', '').isdigit() else 0,
                                "unit": cells[-1] if cells[-1] else "万吨",
                                "period": "annual",
                                "source": "CAISI",
                                "url": "",
                                "crawl_date": datetime.now().isoformat()
                            }
                            stats.append(ProductionStats.from_html(stat_data))
                            
        except Exception as e:
            print(f"Error extracting production stats: {e}")
            
        return stats


class CAISIDocumentFetcher:
    """Fetch detailed document content"""
    
    def __init__(self, base_url: str = "http://www.caisi.org.cn"):
        self.base_url = base_url
        
    async def fetch_document_detail(self, url: str) -> Dict[str, str]:
        """Fetch detailed content of a policy document"""
        try:
            from scrapling import BrowserConfig, AsyncSession
            
            async with AsyncSession(config=BrowserConfig(browser='chrome')) as session:
                response = await session.request('GET', url)
                html = await response.text()
                
                # Extract main content
                content_pattern = r'<div[^>]*id="content"[^>]*>(.*?)</div>|<div[^>]*class="content"[^>]*>(.*?)</div>'
                content_match = re.search(content_pattern, html, re.DOTALL)
                
                content = content_match.group(1) or content_match.group(2) if content_match else ""
                
                return {
                    "content": content,
                    "success": True
                }
                
        except Exception as e:
            return {
                "content": "",
                "success": False,
                "error": str(e)
            }
