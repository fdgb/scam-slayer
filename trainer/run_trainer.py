#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
营销号鉴别训练系统 — 主控脚本
统一调度：采集 → 鉴别 → 归档 → 生成周报
用法：
  python run_trainer.py           # 执行完整流程
  python run_trainer.py --collect # 仅采集
  python run_trainer.py --verdict # 仅鉴别归档
  python run_trainer.py --report  # 仅生成报告
"""

import sys
import os
import json
from datetime import datetime

BASE_DIR = os.path.dirname(__file__)
sys.path.insert(0, BASE_DIR)


def run_collect():
    """执行数据采集"""
    print("\n" + "="*60)
    print("  📡 阶段一：多平台数据采集")
    print("="*60)

    total_new = 0

    try:
        from collect_bilibili import collect_bilibili
        new = collect_bilibili()
        total_new += new
    except Exception as e:
        print(f"  ❌ B站采集失败: {e}")

    try:
        from collect_wechat_weibo import collect_wechat_weibo
        wx_new, wb_new = collect_wechat_weibo()
        total_new += wx_new + wb_new
    except Exception as e:
        print(f"  ❌ 微信/微博采集失败: {e}")

    try:
        from collect_overseas import collect_overseas as collect_overseas_md
        overseas_new = collect_overseas_md()
        total_new += overseas_new
    except Exception as e:
        print(f"  ❌ 海外平台采集失败: {e}")

    print(f"\n  📊 采集阶段完成，共新增 {total_new} 条数据\n")
    return total_new


def run_verdict():
    """执行自动鉴别"""
    print("\n" + "="*60)
    print("  🤖 阶段二：AI 自动鉴别归档")
    print("="*60)

    try:
        from auto_verdict import run_verdict as _run_verdict
        processed, high_risk = _run_verdict()
        return processed, high_risk
    except Exception as e:
        print(f"  ❌ 鉴别失败: {e}")
        return 0, 0


def generate_report():
    """生成统计报告"""
    print("\n" + "="*60)
    print("  📊 阶段三：生成训练报告")
    print("="*60)

    collected_dir = os.path.join(BASE_DIR, "..", "references", "collected_data")
    stats_file = os.path.join(collected_dir, "stats.json")

    stats = {}
    if os.path.exists(stats_file):
        with open(stats_file, "r", encoding="utf-8") as f:
            stats = json.load(f)

    # 统计各队列情况
    queue_summary = {}
    for platform, filename in [("bilibili", "bilibili_queue.json"),
                                 ("wechat", "wechat_queue.json"),
                                 ("weibo", "weibo_queue.json"),
                                 ("overseas", "overseas_queue.json")]:
        filepath = os.path.join(collected_dir, filename)
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            total = len(data)
            verdicted = len([i for i in data if i.get("verdict")])
            high_risk = len([i for i in data if "高危" in i.get("risk_level", "")])
            queue_summary[platform] = {
                "total": total, "verdicted": verdicted,
                "pending": total - verdicted, "high_risk": high_risk
            }

    report_date = datetime.now().strftime("%Y-%m-%d %H:%M")
    report_lines = [
        f"# 🎯 营销号鉴别训练系统 — 运行报告",
        f"**生成时间**: {report_date}",
        f"",
        f"## 📊 数据库总览",
        f"",
        f"| 平台 | 累计采集 | 已鉴别 | 待鉴别 | 高危数量 |",
        f"|------|---------|--------|--------|---------|",
    ]

    total_all = 0
    for platform, summary in queue_summary.items():
        platform_names = {"bilibili": "B站", "wechat": "微信公众号", "weibo": "微博", "overseas": "🌍海外平台(YouTube/Instagram等)"}
        name = platform_names.get(platform, platform)
        report_lines.append(
            f"| {name} | {summary['total']} | {summary['verdicted']} | {summary['pending']} | 🔴{summary['high_risk']} |"
        )
        total_all += summary["total"]

    report_lines += [
        f"",
        f"**数据总量**: {total_all} 条",
        f"",
        f"## 🔍 风险分布",
        f"",
        f"- 🔴 **高危**（已归档到案例库）：{stats.get('high_risk', 0)} 条",
        f"- 🟡 **存疑**（已归档到案例库）：{stats.get('suspicious', 0)} 条",
        f"- 🟢 **可信**：{stats.get('safe', 0)} 条",
        f"",
        f"## 🗓️ 采集关键词覆盖",
        f"",
        f"### 视频平台 (B站)",
        f"副业赚钱 / 月入过万 / 养生秘方 / 保健品推荐 / 食物相克 / 这样吃致癌 / 国家补贴 / 养老金新规 / AI换脸 / 克隆声音",
        f"",
        f"### 图文平台 (微信公众号 / 微博)",
        f"养生保健 / 老年人保健品 / 退休养老金新规 / 治病偏方 / 投资理财稳赚 / 副业月入过万 / AI换脸骗局 / 声音克隆诈骗 / 老人防骗 / 微信群谣言",
        f"",
        f"## 📁 文件路径",
        f"",
        f"- **案例库**: `.agent/skills/scam-slayer/references/case-library.md`",
        f"- **B站队列**: `.agent/skills/scam-slayer/references/collected_data/bilibili_queue.json`",
        f"- **微信队列**: `.agent/skills/scam-slayer/references/collected_data/wechat_queue.json`",
        f"- **微博队列**: `.agent/skills/scam-slayer/references/collected_data/weibo_queue.json`",
        f"- **海外队列**: `.agent/skills/scam-slayer/references/collected_data/overseas_queue.json` (含多语言翻译)",
        f"",
        f"## 🌍 海外平台关键词覆盖",
        f"英文/日文/韩文/法文/德文/俄文/西班牙语等多语言采集",
        f"包含：YouTube、Instagram、Facebook 等平台的营销号/诈骗内容",
        f"所有海外内容均翻译为中文供分析使用",
        f"",
        f"---",
        f"*由 scam-slayer 训练系统自动生成*",
    ]

    report_path = os.path.join(collected_dir, "training_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))

    print("\n".join(report_lines))
    print(f"\n  📄 报告已保存至: {report_path}\n")
    return report_path


def main():
    args = sys.argv[1:]
    mode = args[0] if args else "--all"

    print(f"\n{'='*60}")
    print(f"  🛡️  Marketing-Detector 训练系统")
    print(f"  ⏰  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    if mode in ("--all", "--collect"):
        run_collect()

    if mode in ("--all", "--verdict"):
        run_verdict()

    if mode in ("--all", "--report"):
        generate_report()

    print(f"\n🎉 训练系统执行完毕！\n")


if __name__ == "__main__":
    main()
