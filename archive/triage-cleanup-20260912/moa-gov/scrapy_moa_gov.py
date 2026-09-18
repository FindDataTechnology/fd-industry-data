"""
Scrapyling Spider for Ministry of Agriculture Government Information
Source: http://www.moa.gov.cn/zwllm/
Score: 90

Data Types:
- 部门规章 (Department regulations)
- 政策文件 (Policy documents)
- 农业新闻 (Agricultural news)
- 通知公告 (Notices and announcements)
- 统计数据 (Statistical data)
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
import re
import json


@dataclass
class RegulationDocument:
    """Regulation document structure"""
    title: str
    publish_date: str
    issue_date: str
    issuing_department: str
    doc_number: str
    regulation_type: str
    url: str
    summary: str
    content: str
    tags: List[str]
    crawl_date: str
    
    @classmethod
    def from_html(cls, html_data: Dict[str, Any]) -> "RegulationDocument":
        return cls(
            title=html_data.get("title", ""),
            publish_date=html_data.get("publish_date", ""),
            issue_date=html_data.get("issue_date", ""),
            issuing_department=html_data.get("issuing_department", ""),
            doc_number=html_data.get("doc_number", ""),
            regulation_type=html_data.get("regulation_type", ""),
            url=html_data.get("url", ""),
            summary=html_data.get("summary", ""),
            content=html_data.get("content", ""),
            tags=html_data.get("tags", []),
            crawl_date=datetime.now().isoformat()
        )


@dataclass
class PolicyNotice:
    """Policy notice structure"""
    title: str
    publish_date: str
    department: str
    notice_type: str
    category: str
    url: str
    summary: str
    attachment_urls: List[str]
    keywords: List[str]
    crawl_date: str
    
    @classmethod
    def from_html(cls, html_data: Dict[str, Any]) -> "PolicyNotice":
        return cls(
            title=html_data.get("title", ""),
            publish_date=html_data.get("publish_date", ""),
            department=html_data.get("department", ""),
            notice_type=html_data.get("notice_type", ""),
            category=html_data.get("category", ""),
            url=html_data.get("url", ""),
            summary=html_data.get("summary", ""),
            attachment_urls=html_data.get("attachment_urls", []),
            keywords=html_data.get("keywords", []),
            crawl_date=datetime.now().isoformat()
        )


@dataclass
class AgriculturalNews:
    """Agricultural news structure"""
    headline: str
    publish_date: str
    source: str
    author: str
    category: str
    location: Optional[str]
    content: str
    images: List[str]
    related_topics: List[str]
    url: str
    crawl_date: str
    
    @classmethod
    def from_html(cls, html_data: Dict[str, Any]) -> "AgriculturalNews":
        return cls(
            headline=html_data.get("headline", ""),
            publish_date=html_data.get("publish_date", ""),
            source=html_data.get("source", ""),
            author=html_data.get("author", ""),
            category=html_data.get("category", ""),
            location=html_data.get("location"),
            content=html_data.get("content", ""),
            images=html_data.get("images", []),
            related_topics=html_data.get("related_topics", []),
            url=html_data.get("url", ""),
            crawl_date=datetime.now().isoformat()
        )


class MoAGovExtractor:
    """Extract structured data from MoA.gov.cn HTML content"""
    
    def __init__(self):
        self.base_url = "http://www.moa.gov.cn"
        
    def extract_regulations(self, html_content: str, base_url: str = "") -> List[RegulationDocument]:
        """Extract regulation documents from HTML"""
        regulations = []
        
        try:
            # Find list items
            list_pattern = r'<div[^>]*class="li-item"[^>]*>(.*?)</div>|<li[^>]*>(.*?)</li>'
            matches = re.findall(list_pattern, html_content, re.DOTALL)
            
            for match in matches:
                item = match[0] or match[1]
                
                # Extract title
                title_match = re.search(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', item, re.DOTALL)
                if not title_match:
                    continue
                    
                link = title_match.group(1)
                title = re.sub(r'<[^>]+>', '', title_match.group(2)).strip()
                
                # Extract date
                date_match = re.search(r'<span[^>]*class="date"(.*?)</span>|<span[^>]*>(\d{4}-\d{2}-\d{2})', item, re.DOTALL)
                date_str = ""
                if date_match:
                    if date_match.group(2):
                        date_str = date_match.group(2)
                    else:
                        date_str = re.sub(r'<[^>]+>', '', date_match.group(1)).strip()
                
                # Construct URL
                full_url = f"{base_url}{link}" if link.startswith('/') else link
                
                reg_data = {
                    "title": title,
                    "publish_date": date_str,
                    "issue_date": "",
                    "issuing_department": "MOA",
                    "doc_number": "",
                    "regulation_type": "regulation",
                    "url": full_url,
                    "summary": "",
                    "content": "",
                    "tags": [],
                    "crawl_date": datetime.now().isoformat()
                }
                regulations.append(RegulationDocument.from_html(reg_data))
                
        except Exception as e:
            print(f"Error extracting regulations: {e}")
            
        return regulations
    
    def extract_notices(self, html_content: str, base_url: str = "") -> List[PolicyNotice]:
        """Extract policy notices from HTML"""
        notices = []
        
        try:
            pattern = r'<div[^>]*class="news-list"(.*?)</div>'
            container = re.search(pattern, html_content, re.DOTALL)
            
            if container:
                items = re.findall(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', container.group(1))
                
                for link, title in items:
                    # Try to extract date nearby
                    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', html_content[html_content.find(link)-50:html_content.find(link)+50])
                    
                    full_url = f"{base_url}{link}" if link.startswith('/') else link
                    
                    notice_data = {
                        "title": re.sub(r'<[^>]+>', '', title).strip(),
                        "publish_date": date_match.group(1) if date_match else "",
                        "department": "MOA",
                        "notice_type": "notice",
                        "category": "policy",
                        "url": full_url,
                        "summary": "",
                        "attachment_urls": [],
                        "keywords": [],
                        "crawl_date": datetime.now().isoformat()
                    }
                    notices.append(PolicyNotice.from_html(notice_data))
                    
        except Exception as e:
            print(f"Error extracting notices: {e}")
            
        return notices
    
    def extract_news(self, html_content: str, base_url: str = "") -> List[AgriculturalNews]:
        """Extract agricultural news from HTML"""
        news_list = []
        
        try:
            # Find news items
            news_pattern = r'<div[^>]*class="article-item"[^>]*>(.*?)</div>'
            articles = re.findall(news_pattern, html_content, re.DOTALL)
            
            for article in articles:
                title_match = re.search(r'<h2[^>]*>(.*?)</h2>', article, re.DOTALL)
                link_match = re.search(r'<a[^>]*href="([^"]*)"', article)
                date_match = re.search(r'\d{4}-\d{2}-\d{2}', article)
                source_match = re.search(r'来源[:：]\s*([^|<\n]+)', article)
                
                if title_match and link_match:
                    link = link_match.group(1)
                    full_url = f"{base_url}{link}" if link.startswith('/') else link
                    
                    news_data = {
                        "headline": re.sub(r'<[^>]+>', '', title_match.group(1)).strip(),
                        "publish_date": date_match.group(0) if date_match else "",
                        "source": re.sub(r'<[^>]+>', '', source_match.group(1)).strip() if source_match else "MOA",
                        "author": "",
                        "category": "agriculture",
                        "location": None,
                        "content": "",
                        "images": [],
                        "related_topics": [],
                        "url": full_url,
                        "crawl_date": datetime.now().isoformat()
                    }
                    news_list.append(AgriculturalNews.from_html(news_data))
                    
        except Exception as e:
            print(f"Error extracting news: {e}")
            
        return news_list


class MoAGovAPIHelper:
    """Helper for API-based queries on MoA.gov.cn"""
    
    # Search endpoint parameters
    SEARCH_PARAMS = {
        "q": "search query",
        "channelid": "channel ID (zhengwu/news/etc)",
        "page": "page number",
        "pagesize": "items per page"
    }
    
    # List endpoint parameters  
    LIST_PARAMS = {
        "channelid": "required channel identifier",
        "page": "page number",
        "sorttype": "sort type",
        "show": "number of items"
    }
    
    @staticmethod
    def build_search_url(base_url: str, params: Dict[str, str]) -> str:
        """Build search URL with parameters"""
        query_params = "&".join([f"{k}={v}" for k, v in params.items()])
        return f"{base_url}?{query_params}"
    
    @staticmethod
    def get_channel_ids() -> Dict[str, str]:
        """Return common channel IDs for MoA website"""
        return {
            "zwgk": "政务公开",
            "xxgkml": "部公文公开",
            "zcfb": "政策发布",
            "bgjj": "部发布会",
            "dtmt": "多媒体",
            "tjgb": "统计公报",
            "jdgg": "重点公告",
            "zcwj": "政策法规",
            "flfg": "法律法规",
            "xwdt": "新闻报道"
        }
