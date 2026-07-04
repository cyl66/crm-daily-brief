"""
CRM日报平台 - 日报组装器
将处理后数据组装为结构化报告
"""
import json
from datetime import datetime
from pathlib import Path


class Assembler:
    def __init__(self, config_path: str = "config/config.json"):
        with open(config_path) as f:
            self.config = json.load(f)
        self.sections = self.config["sections"]
        self.archive_dir = Path(self.config["archive"]["path"])
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.web_dir = Path("web")
        self.web_dir.mkdir(parents=True, exist_ok=True)

    def assemble_brief(self, sections: dict, target_date: str) -> dict:
        """组装完整日报JSON"""
        brief = {
            "date": target_date,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "sections": {},
            "total_articles": 0,
            "coverage_summary": {}
        }

        for section_id in ['A', 'B', 'C', 'D', 'E', 'F']:
            section_config = self.sections[section_id]
            items = sections.get(section_id, [])

            brief["sections"][section_id] = {
                "name": section_config["name"],
                "max_items": section_config["max_items"],
                "items": [],
                "count": 0
            }

            for item in items:
                brief["sections"][section_id]["items"].append({
                    "title": item.get("title", ""),
                    "summary": item.get("summary", ""),
                    "url": item.get("url", ""),
                    "source": item.get("source_name", ""),
                    "importance_level": item.get("importance_level", "low"),
                    "importance_score": item.get("importance_score", 0),
                    "sentiment": item.get("sentiment"),
                    "companies": item.get("companies", []),
                    "is_our_brand": item.get("is_our_brand", 0),
                })
                brief["sections"][section_id]["count"] += 1
                brief["total_articles"] += 1

            brief["coverage_summary"][section_id] = {
                "name": section_config["name"],
                "count": brief["sections"][section_id]["count"],
                "sources_covered": len(set(
                    item.get("source_name", "") for item in items if item.get("source_name")
                ))
            }

        return brief

    def save_brief(self, brief: dict):
        """保存日报到归档文件"""
        target_date = brief["date"]
        archive_file = self.archive_dir / f"{target_date}.json"
        with open(archive_file, "w", encoding="utf-8") as f:
            json.dump(brief, f, ensure_ascii=False, indent=2)

        latest_file = Path(self.config["web"]["latest_json"])
        with open(latest_file, "w", encoding="utf-8") as f:
            json.dump(brief, f, ensure_ascii=False, indent=2)

        return archive_file

    def build_wecom_message(self, brief: dict) -> str:
        """构建企微Markdown推送消息"""
        date = brief["date"]
        total = brief["total_articles"]

        lines = [
            f"## 📋 CRM行业日报 | {date}",
            f"> 共收录 {total} 条信息",
            "",
        ]

        cn_num = {"A": "一", "B": "二", "C": "三", "D": "四", "E": "五", "F": "六"}
        web_url = self.config.get("web", {}).get("site_url", "https://cyl66.github.io/crm-daily-brief/web/")

        for section_id in ['A', 'B', 'C', 'D', 'E', 'F']:
            section_data = brief["sections"].get(section_id, {})
            count = section_data.get("count", 0)
            name = section_data.get("name", "")
            num = cn_num.get(section_id, "?")

            if count == 0:
                lines.append(f"**{num}、{name}**：本日无相关动态")
            else:
                lines.append(f"**{num}、{name}（{count}条）**")
                items = section_data.get("items", [])
                for i, item in enumerate(items[:3]):
                    imp_mark = "⭐" if item.get("importance_level") == "high" else ""
                    sentiment_mark = ""
                    if section_id == 'C' and item.get("sentiment") == "negative":
                        sentiment_mark = "🔴"
                    elif section_id == 'C' and item.get("sentiment") == "positive":
                        sentiment_mark = "🟢"
                    title = item.get("title", "")[:35]
                    url = item.get("url", "")
                    lines.append(f"{i+1}. {imp_mark}{sentiment_mark} [{title}]({url})")
            # 板块间加两个空行确保明显间隔
            lines.append("")
            lines.append("")

        # 最后一个板块与链接之间也间隔
        lines.append("")
        lines.append(f"[📖 查看完整日报]({web_url})")

        return "\n".join(lines)

    def aggregate_brief(self, start_date: str, end_date: str, archive_files: list = None) -> dict:
        """
        聚合多日日报（支持周/月/季度颗粒度）
        """
        aggregated = {
            "date_range": f"{start_date} ~ {end_date}",
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "sections": {},
            "total_articles": 0,
            "days_covered": 0,
        }

        section_aggregator = {s: {"name": self.sections[s]["name"], "items": []} for s in ['A','B','C','D','E','F']}

        if archive_files:
            for af in archive_files:
                try:
                    with open(af, encoding="utf-8") as f:
                        daily = json.load(f)
                    aggregated["days_covered"] += 1
                    for section_id in ['A','B','C','D','E','F']:
                        items = daily.get("sections", {}).get(section_id, {}).get("items", [])
                        for item in items:
                            item["_date"] = daily.get("date", "")
                        section_aggregator[section_id]["items"].extend(items)
                except Exception:
                    continue
        else:
            for f in sorted(self.archive_dir.glob("*.json")):
                date_str = f.stem
                if start_date <= date_str <= end_date:
                    try:
                        with open(f, encoding="utf-8") as fh:
                            daily = json.load(fh)
                        aggregated["days_covered"] += 1
                        for section_id in ['A','B','C','D','E','F']:
                            items = daily.get("sections", {}).get(section_id, {}).get("items", [])
                            for item in items:
                                item["_date"] = date_str
                            section_aggregator[section_id]["items"].extend(items)
                    except Exception:
                        continue

        for section_id in ['A','B','C','D','E','F']:
            aggregated_items = section_aggregator[section_id]["items"]
            aggregated_items.sort(key=lambda x: x.get("_date", ""), reverse=True)
            aggregated["sections"][section_id] = {
                "name": self.sections[section_id]["name"],
                "items": aggregated_items,
                "count": len(aggregated_items),
            }
            aggregated["total_articles"] += len(aggregated_items)

        return aggregated
