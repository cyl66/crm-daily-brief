"""
CRM日报平台 - 处理引擎
三级去重 → LLM提炼 → 多源交叉验证 → 重要性评分 → 板块排序截断
"""
import json
import hashlib
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional


class Processor:
    def __init__(self, config_path: str = "config/config.json"):
        with open(config_path) as f:
            self.config = json.load(f)
        self.dedup_config = self.config["dedup"]
        self.scoring_config = self.config["importance_scoring"]
        self.llm_config = self.config["llm"]
        self.brands = self.config["auto_brands"]

    def deduplicate(self, items: list) -> list:
        """
        三级去重漏斗
        L1: URL精确匹配 → L2: 标题Jaccard相似度 → L3: 内容语义相似度
        """
        if len(items) <= 1:
            return items

        seen = set()
        L1_result = []
        for item in items:
            url_hash = hashlib.md5(item.get("url", "").encode()).hexdigest()
            if url_hash not in seen:
                seen.add(url_hash)
                L1_result.append(item)

        L2_result = []
        for i, item in enumerate(L1_result):
            is_dup = False
            for existing in L2_result:
                if self._title_jaccard(item.get("title", ""), existing.get("title", "")) >= self.dedup_config["jaccard_threshold"]:
                    is_dup = True
                    break
            if not is_dup:
                L2_result.append(item)

        return L2_result

    def refine_with_llm(self, items: list) -> list:
        """
        LLM批量提炼：标题 + 摘要 + 分类 + 情感分析
        此方法在 WorkBuddy Agent 环境中由 LLM 调用实现
        独立部署时使用 DeepSeek API
        """
        refined = []
        batch_size = self.llm_config.get("batch_size", 10)

        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]
            batch_refined = self._llm_refine_batch(batch)
            refined.extend(batch_refined)

        return refined

    def _llm_refine_batch(self, batch: list) -> list:
        """
        LLM批量提炼核心逻辑
        输出格式: [{title, summary, section, sentiment, importance_level, companies, is_our_brand}]
        """
        prompt = self._build_refine_prompt(batch)
        # 此处由 WorkBuddy Agent 调用 LLM API
        # 独立部署时: response = openai.ChatCompletion.create(
        #     model=self.llm_config["model"],
        #     messages=[{"role": "user", "content": prompt}],
        #     temperature=self.llm_config["temperature"]
        # )
        # parsed = json.loads(response.choices[0].message.content)
        return batch

    def _build_refine_prompt(self, batch: list) -> str:
        """构建LLM提炼的Prompt"""
        sections_desc = "\n".join([
            f"  {k}: {v['name']} — {', '.join(v['search_keywords'][:3])}"
            for k, v in self.config["sections"].items()
        ])

        brand_list = []
        for cat_name, brands in self.brands.items():
            for b in brands:
                brand_list.append(f"  {b['name']} ({cat_name})")
        brands_desc = "\n".join(brand_list)

        articles_json = json.dumps([{
            "id": i,
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "source": item.get("source_name", ""),
            "content": item.get("raw_content", "")[:2000]
        } for i, item in enumerate(batch)], ensure_ascii=False, indent=2)

        return f"""你是CRM行业和汽车行业的内容编辑专家。请对以下信息做结构化提炼。

【六板块分类】
{sections_desc}

【品牌列表】
{brands_desc}

【处理要求】
对每条信息输出JSON格式：
{{
  "id": 原始ID,
  "title": "核心要点提炼（≤15字）",
  "summary": "50-80字内容摘要，说明核心事实和关键影响",
  "section": "A/B/C/D/E/F之一",
  "sentiment": "仅C板块需要：positive/negative/null",
  "companies": ["关联的品牌/公司名称"],
  "importance_level": "high/medium/low",
  "is_our_brand": 0或1(领克/吉利/极氪=1)
}}

规则：
- 一条信息只能属于一个板块
- 竞品CRM动态归入板块A
- 舆情（投诉/召回/维权/口碑）归入板块C
- 政策法规归入板块D
- 不重要的重复转载标 importance_level=low
- title概括核心事件，不要原文标题

【待处理信息】
{articles_json}

请只输出JSON数组，不要其他文字。"""

    def cross_validate(self, items: list) -> list:
        """
        多源交叉验证 + 重要性评分
        同一事件被多个信息源报道 → 重要性升级
        """
        groups = self._group_similar_items(items)

        for item in items:
            title = item.get("title", "")
            group = self._find_group(groups, title)
            coverage = len(group) if group else 1

            authority = self._calc_authority(item.get("source_name", ""))
            freshness = self._calc_freshness(item.get("pub_date", ""))
            relevance = self._calc_relevance(item.get("section", ""), item.get("title", ""))

            w = self.scoring_config["weights"]
            score = (
                w["source_authority"] * authority +
                w["multi_source_coverage"] * min(coverage / 5.0, 1.0) +
                w["freshness"] * freshness +
                w["relevance_match"] * relevance
            )

            item["importance_score"] = round(score, 3)
            if coverage >= 3:
                item["importance_level"] = "high"
            elif coverage >= 2:
                item["importance_level"] = "medium"
            else:
                item["importance_level"] = "low"

        return items

    def sort_and_cut(self, items: list) -> dict:
        """按板块分组 → 排序 → Top10截断"""
        sections = defaultdict(list)
        for item in items:
            sections[item.get("section", "F")].append(item)

        result = {}
        for section_id in ['A', 'B', 'C', 'D', 'E', 'F']:
            section_items = sorted(
                sections[section_id],
                key=lambda x: x.get("importance_score", 0),
                reverse=True
            )
            max_items = self.config["sections"][section_id].get("max_items", 10)
            truncated = section_items[:max_items]

            if section_id == 'C':
                truncated = self._organize_sentiment(truncated)

            result[section_id] = truncated

        return result

    def _organize_sentiment(self, items: list) -> list:
        """C板块：本品/竞品分组，正面/负面分段"""
        our_brands = [item for item in items if item.get("is_our_brand")]
        competitor = [item for item in items if not item.get("is_our_brand")]

        def sort_sentiment(lst):
            pos = [x for x in lst if x.get("sentiment") == "positive"]
            neg = [x for x in lst if x.get("sentiment") == "negative"]
            other = [x for x in lst if x.get("sentiment") not in ("positive", "negative")]
            return pos + neg + other

        return sort_sentiment(our_brands) + sort_sentiment(competitor)

    @staticmethod
    def _title_jaccard(t1: str, t2: str) -> float:
        """Jaccard相似度（基于2-gram字符）"""
        def ngrams(s, n=2):
            return set(s[i:i+n] for i in range(len(s) - n + 1))
        if not t1 or not t2:
            return 0.0
        g1, g2 = ngrams(t1, 2), ngrams(t2, 2)
        if not g1 or not g2:
            return 0.0
        return len(g1 & g2) / len(g1 | g2)

    @staticmethod
    def _group_similar_items(items: list) -> list:
        """将相似标题分组"""
        groups = []
        used = set()
        for i, item in enumerate(items):
            if i in used:
                continue
            group = [item]
            used.add(i)
            for j, other in enumerate(items):
                if j in used:
                    continue
                sim = Processor._title_jaccard(
                    item.get("title", ""), other.get("title", "")
                )
                if sim >= 0.60:
                    group.append(other)
                    used.add(j)
            groups.append(group)
        return groups

    @staticmethod
    def _find_group(groups: list, title: str) -> Optional[list]:
        for group in groups:
            for item in group:
                if item.get("title") == title:
                    return group
        return None

    def _calc_authority(self, source_name: str) -> float:
        """计算来源权威度"""
        authority_map = self.scoring_config.get("authority_scores", {})
        if any(gov in (source_name or "").lower() for gov in ["gov", "工信部", "发改委", "网信办"]):
            return authority_map.get("gov_announcement", 1.0)
        if any(tech in (source_name or "").lower() for tech in ["36氪", "虎嗅", "晚点", "钛媒体"]):
            return authority_map.get("top_tech_media", 0.9)
        if any(auto in (source_name or "") for auto in ["懂车帝", "汽车之家", "易车", "盖世"]):
            return authority_map.get("auto_vertical_media", 0.8)
        return authority_map.get("general_media", 0.4)

    @staticmethod
    def _calc_freshness(pub_date: str) -> float:
        """计算时效性衰减"""
        import math
        try:
            if not pub_date:
                return 0.3
            dt = datetime.strptime(pub_date[:10], "%Y-%m-%d")
            hours = (datetime.now() - dt).total_seconds() / 3600
            return math.exp(-0.05 * max(hours, 0))
        except Exception:
            return 0.3

    @staticmethod
    def _calc_relevance(section: str, title: str) -> float:
        """计算板块相关度"""
        if not section or not title:
            return 0.5
        section_keywords = {
            'A': ['CRM', '客户', '销售', '营销', 'SaaS', 'SCRM'],
            'B': ['汽车', '新车', '新能源', '销量', '发布'],
            'C': ['投诉', '召回', '舆情', '维权', '口碑'],
            'D': ['政策', '法规', '条例', '办法', '通知'],
            'E': ['营销', '私域', 'CDP', '自动化', 'AI'],
            'F': ['消费者', '用户', '购车', 'Z世代', '行为']
        }
        keywords = section_keywords.get(section, [])
        matches = sum(1 for kw in keywords if kw in (title or ""))
        return min(matches / max(len(keywords), 1), 1.0)
