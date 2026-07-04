"""
CRM日报平台 - 采集引擎
多渠道采集：WebSearch(百度新闻+头条搜索)、RSS、微信公众号
"""
import json
import hashlib
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import quote


class Collector:
    """多渠道信息采集引擎"""

    def __init__(self, config_path: str = "config/config.json"):
        with open(config_path) as f:
            self.config = json.load(f)
        self.sections = self.config["sections"]
        self.keywords_cache = {}
        self.rss_feeds = self.config.get("rss_feeds", [])

    def collect_all(self, target_date: str) -> list:
        """采集六板块信息"""
        all_items = []
        for section_id in ['A', 'B', 'C', 'D', 'E', 'F']:
            section_config = self.sections[section_id]
            section_items = self._collect_section(section_id, section_config, target_date)
            all_items.extend(section_items)
        return all_items

    def _collect_section(self, section_id: str, section_config: dict, target_date: str) -> list:
        """采集单个板块"""
        items = []
        sources = section_config.get("sources", [])

        if "websearch" in sources:
            keywords = section_config.get("search_keywords", [])
            for keyword in keywords:
                results = self._search_web(keyword, section_id, target_date)
                items.extend(results)

        if "rss" in sources:
            rss_items = self._fetch_rss_feeds(section_id, target_date)
            items.extend(rss_items)

        if "wechat" in sources:
            wechat_items = self._search_wechat(keywords, section_id, target_date)
            items.extend(wechat_items)

        if "auto_vertical" in sources:
            auto_items = self._fetch_auto_vertical(section_config, target_date)
            items.extend(auto_items)

        for item in items:
            item["section"] = section_id
            item["pub_date"] = target_date
            if "content_hash" not in item:
                item["content_hash"] = hashlib.md5(item.get("url", "").encode()).hexdigest()

        return items

    def _search_web(self, keyword: str, section_id: str, target_date: str) -> list:
        """
        通过 WebSearch 工具搜索信息
        返回格式：[{title, url, summary, source_name, raw_content}]
        """
        items = []

        # 拼接搜索词
        search_query = f"{keyword} {target_date}"

        # 特殊处理——CRM板块叠加公司关键词
        if section_id == 'A':
            crm_keywords = self._get_crm_keywords()
            for ck in crm_keywords[:3]:
                items.extend(self._do_web_search(f"{ck} {keyword}"))

        # 特殊处理——舆情板块叠加品牌词
        elif section_id == 'C':
            brand_keywords = self._get_brand_keywords()
            for bk in brand_keywords[:5]:
                items.extend(self._do_web_search(f"{bk} 舆情 最新"))

        # 特殊处理——政策板块搜索政府网站
        elif section_id == 'D':
            for gov in section_config.get("gov_sources", [])[:3]:
                items.extend(self._do_web_search(f"site:{gov['url']} {keyword}"))

        # 通用搜索
        items.extend(self._do_web_search(search_query))

        return items

    def _do_web_search(self, query: str) -> list:
        """
        实际执行 WebSearch 的工具调用占位
        注意：此方法在 WorkBuddy 环境中由 Agent 调用 WebSearch 工具实现
        独立运行时使用 requests + 搜索引擎API
        """
        # 此方法在 WorkBuddy Agent 环境中通过 WebSearch 工具实现
        # 独立部署时，使用 requests + DuckDuckGo/Bing API
        return []

    def _fetch_rss_feeds(self, section_id: str, target_date: str) -> list:
        """拉取 RSS 源"""
        items = []
        for feed_url in self.rss_feeds:
            try:
                import feedparser
                feed = feedparser.parse(feed_url)
                for entry in feed.entries:
                    pub_date = self._parse_feed_date(entry)
                    if pub_date and self._is_target_date(pub_date, target_date):
                        items.append({
                            "title": entry.get("title", ""),
                            "url": entry.get("link", ""),
                            "summary": entry.get("summary", ""),
                            "source_name": feed.feed.get("title", feed_url),
                            "raw_content": entry.get("summary", ""),
                        })
            except Exception:
                continue
        return items

    def _fetch_auto_vertical(self, section_config: dict, target_date: str) -> list:
        """采集汽车垂直媒体"""
        items = []
        sources = section_config.get("auto_vertical_sources", [])
        for src in sources:
            keyword = f"site:{src['url']} 最新 {target_date}"
            items.extend(self._do_web_search(keyword))
        return items

    def _search_wechat(self, keywords: list, section_id: str, target_date: str) -> list:
        """
        微信公众号文章搜索
        通过 wechat-article-search skill 实现
        """
        items = []
        for kw in keywords[:3]:
            items.extend(self._do_web_search(f"{kw} 微信公众号"))
        return items

    def _get_crm_keywords(self) -> list:
        """获取CRM公司关键词"""
        companies = []
        for cat in ["overseas", "domestic"]:
            for co in self.config["crm_companies"][cat]:
                companies.append(co["name"])
        return companies

    def _get_brand_keywords(self) -> list:
        """获取品牌关键词"""
        brands = []
        for cat in ["our_brands", "competitor_brands"]:
            for b in self.config["auto_brands"][cat]:
                brands.append(b["name"])
        return brands

    @staticmethod
    def _parse_feed_date(entry) -> Optional[str]:
        """解析RSS中的日期"""
        from datetime import datetime
        for attr in ["published_parsed", "updated_parsed"]:
            parsed = getattr(entry, attr, None)
            if parsed:
                try:
                    dt = datetime(*parsed[:6])
                    return dt.strftime("%Y-%m-%d")
                except Exception:
                    continue
        return None

    @staticmethod
    def _is_target_date(date_str: str, target_date: str) -> bool:
        """判断日期是否为目标日期"""
        return date_str[:10] == target_date[:10]
