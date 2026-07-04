"""
CRM日报平台 - 主调度器
全流程编排：采集 → 处理 → 组装 → 存储 → 推送
"""
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from database import Database
from collector import Collector
from processor import Processor
from assembler import Assembler
from pusher import Pusher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("crm_brief")


def daily_pipeline(target_date: str = None):
    """
    日报生成主流程
    1. 采集 → 2. 去重 → 3. LLM提炼 → 4. 交叉验证 → 5. 排序截断 → 6. 组装归档 → 7. 推送
    """
    if target_date is None:
        target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    logger.info(f"========== 日报生成开始 | {target_date} ==========")

    # 初始化
    config_path = Path(__file__).parent.parent / "config" / "config.json"
    db = Database(str(Path(__file__).parent.parent / "data" / "crm_brief.db"))
    collector = Collector(str(config_path))
    processor = Processor(str(config_path))
    assembler = Assembler(str(config_path))
    pusher = Pusher(str(config_path))

    # Step 1: 采集
    logger.info("Step 1/7: 多渠道采集")
    raw_items = collector.collect_all(target_date)
    logger.info(f"  采集原始数据: {len(raw_items)} 条")

    # Step 2: 去重
    logger.info("Step 2/7: 三级去重")
    deduped = processor.deduplicate(raw_items)
    logger.info(f"  去重后: {len(deduped)} 条")

    # Step 3: LLM提炼
    logger.info("Step 3/7: LLM智能提炼")
    refined = processor.refine_with_llm(deduped)
    logger.info(f"  提炼完成: {len(refined)} 条")

    # Step 4: 多源交叉验证 + 重要性评分
    logger.info("Step 4/7: 多源交叉验证与评分")
    scored = processor.cross_validate(refined)

    # Step 5: 排序截断
    logger.info("Step 5/7: 板块排序与Top10截断")
    sorted_sections = processor.sort_and_cut(scored)

    # Step 6: 入库 + 组装 + 归档
    logger.info("Step 6/7: 入库与归档")
    total_count = 0
    for section_id, items in sorted_sections.items():
        for item in items:
            article_id = db.insert_article(item)
            if article_id:
                total_count += 1
    logger.info(f"  入库: {total_count} 条")

    brief = assembler.assemble_brief(sorted_sections, target_date)
    db.save_daily_brief(target_date, brief["sections"], total_count)
    archive_path = assembler.save_brief(brief)
    logger.info(f"  归档: {archive_path}")

    # Step 7: 企微推送
    logger.info("Step 7/7: 企微推送")
    if pusher.should_push_today():
        message = assembler.build_wecom_message(brief)
        success = pusher.push(message)
        if success:
            db.mark_pushed(target_date)
            logger.info("  推送成功 ✅")
        else:
            logger.warning("  推送失败 ⚠️")
    else:
        logger.info("  今日跳过推送")

    # 统计
    stats = db.get_section_stats(target_date)
    logger.info(f"========== 日报生成完成 | 总收录 {total_count} 条 ==========")
    for s in ['A', 'B', 'C', 'D', 'E', 'F']:
        logger.info(f"  {s}: {stats.get(s, 0)} 条")

    return brief


def aggregate_pipeline(start_date: str, end_date: str) -> dict:
    """聚合多日日报"""
    config_path = Path(__file__).parent.parent / "config" / "config.json"
    assembler = Assembler(str(config_path))
    return assembler.aggregate_brief(start_date, end_date)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CRM日报生成系统")
    parser.add_argument("--date", type=str, help="目标日期 (YYYY-MM-DD)，默认昨天")
    parser.add_argument("--aggregate", action="store_true", help="聚合模式")
    parser.add_argument("--start", type=str, help="聚合起始日期")
    parser.add_argument("--end", type=str, help="聚合结束日期")
    parser.add_argument("--test-webhook", action="store_true", help="测试企微Webhook")

    args = parser.parse_args()

    if args.test_webhook:
        pusher = Pusher()
        result = pusher.test_webhook()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.aggregate:
        result = aggregate_pipeline(args.start, args.end)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        daily_pipeline(args.date)
