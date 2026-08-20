#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scam-slayer · B 端批量鉴别接口 + 合规报告 (P0-B / P0-C)
============================================================================
输入一批 URL / 账号 / 文本（JSON 或 CSV），输出：
  1) batch_report_<ts>.json   —— 逐条可追溯鉴别记录（责任兜底证据链）
  2) batch_report_<ts>.csv    —— 运营/风控看板用
  3) compliance_report_<ts>.md —— 正式合规报告（机构「尽到提醒义务」兜底）

复用 monitor.build_item + auto_verdict.rule_engine_verdict / deep_review_verdict。
诚实边界：account/keyword 离线无法抓实时内容，标「⚪需联网检索」；text 类做正文鉴别。

用法：
  python batch_verdict.py input.json [--org 机构名] [--out 输出目录]
  python batch_verdict.py input.csv  [--org 机构名]
输入 JSON 格式：[{"type":"url|account|keyword|text","value":"...","label":"..."}]
输入 CSV 格式：表头 type,value,label
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from monitor import build_item
from auto_verdict import rule_engine_verdict, deep_review_verdict


def verdict_one(entry):
    """对单条输入做鉴别，返回可追溯记录。"""
    etype = entry.get("type", "").lower()
    value = entry.get("value", "")

    # text 类：直接用正文鉴别（B 端常提交文章/话术文本）
    if etype == "text":
        item = {"platform": "text_probe", "title": value[:200], "summary": value, "search_keyword": ""}
        note = "正文内容鉴别"
        can, heur = True, []
    else:
        item, note, can, heur = build_item(entry)

    rec = {
        "type": etype,
        "value": value,
        "label": entry.get("label", ""),
        "verdict_at": datetime.now().isoformat(),
        "source_note": note,
    }
    if not can:
        rec.update({"status": "⚪需联网检索", "risk_level": "⚪未知", "verdict": note, "hit_patterns": []})
        return rec

    v = rule_engine_verdict(item)
    v = deep_review_verdict(item, v)
    hits = list(v.get("hit_patterns", [])) + heur
    rl = v.get("risk_level", "🟡存疑")
    if etype == "url" and heur:
        strong = any(("钓鱼" in h) or ("仿冒" in h) or ("userinfo" in h) for h in heur)
        if strong:
            rl = "🔴高危"
            v["verdict"] = "URL 结构高度可疑（疑似钓鱼/仿冒），需立即人工核实"
        elif "高危" not in rl and "存疑" not in rl:
            rl = "🟡存疑"
            v["verdict"] = "URL 结构存在可疑特征，建议人工核实"
    rec.update({
        "status": "✅已鉴别",
        "risk_level": rl,
        "verdict": v.get("verdict", ""),
        "hit_patterns": hits,
        "reason": v.get("reason", ""),
        "recommendation": v.get("recommendation", ""),
    })
    return rec


def load_inputs(path):
    if path.endswith(".csv"):
        rows = []
        with open(path, "r", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows.append({
                    "type": (r.get("type") or "").strip(),
                    "value": (r.get("value") or "").strip(),
                    "label": (r.get("label") or "").strip(),
                })
        return rows
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [{"type": d.get("type", ""), "value": d.get("value", ""), "label": d.get("label", "")} for d in data]


def run_batch(items, org="（机构名称待填）"):
    """核心批量鉴别（无文件 IO，供 CLI / API / guardian 复用）。
    返回 (results, stats)。"""
    results = []
    for it in items:
        if it.get("type") not in ("url", "account", "keyword", "text"):
            print(f"⚠️  跳过未知类型「{it.get('type')}」")
            continue
        results.append(verdict_one(it))
    stats = {
        "total": len(results),
        "high_risk": sum(1 for r in results if "高危" in r["risk_level"]),
        "suspicious": sum(1 for r in results if "存疑" in r["risk_level"]),
        "need_online": sum(1 for r in results if "未知" in r["risk_level"]),
    }
    return results, stats


def main():
    ap = argparse.ArgumentParser(description="scam-slayer B 端批量鉴别 + 合规报告")
    ap.add_argument("input", help="JSON 或 CSV 输入文件")
    ap.add_argument("--org", default="（机构名称待填）", help="委托机构名（合规报告用）")
    ap.add_argument("--out", default=None, help="输出目录，默认 trainer/monitor_data/batch")
    args = ap.parse_args()

    items = load_inputs(args.input)
    if not items:
        print("❌ 输入为空或格式不正确")
        return

    results, stats = run_batch(items, args.org)
    high, susp, need_online = stats["high_risk"], stats["suspicious"], stats["need_online"]

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    outdir = args.out or os.path.join(BASE_DIR, "monitor_data", "batch")
    os.makedirs(outdir, exist_ok=True)
    json_path = os.path.join(outdir, f"batch_report_{ts}.json")
    csv_path = os.path.join(outdir, f"batch_report_{ts}.csv")
    md_path = os.path.join(outdir, f"compliance_report_{ts}.md")

    # 1) JSON 追溯记录
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "report_type": "scam-slayer 批量鉴别追溯记录",
            "generated_at": datetime.now().isoformat(),
            "engine": "rule_engine_v4 (zero-LLM)",
            "org": args.org,
            "total": len(results),
            "high_risk": high, "suspicious": susp, "need_online": need_online,
            "disclaimer": "结论由规则引擎离线生成，非人工核实；重大风险请以官方渠道为准。",
            "results": results,
        }, f, ensure_ascii=False, indent=2)

    # 2) CSV 运营表
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["type", "value", "label", "risk_level", "verdict", "hit_patterns", "verdict_at"])
        for r in results:
            w.writerow([r["type"], r["value"], r.get("label", ""), r["risk_level"],
                        r.get("verdict", ""), "; ".join(r.get("hit_patterns", [])), r.get("verdict_at", "")])

    # 3) 合规报告 markdown (P0-C)
    _write_compliance(md_path, args.org, ts, results, high, susp, need_online)

    print(f"✅ 批量鉴别完成：共 {len(results)} 条 | 🔴高危 {high} | 🟡存疑 {susp} | ⚪需联网 {need_online}")
    print(f"   JSON 追溯 : {json_path}")
    print(f"   CSV 运营  : {csv_path}")
    print(f"   合规报告  : {md_path}")


def _write_compliance(path, org, ts, results, high, susp, need_online):
    lines = [
        f"# 🛡️ Scam Slayer 批量鉴别合规报告",
        f"",
        f"**报告编号**：SS-BC-{ts}",
        f"**委托机构**：{org}",
        f"**生成时间**：{datetime.now().isoformat()}",
        f"**鉴别引擎**：rule_engine_v4（零 LLM，纯规则，可复现）",
        f"",
        f"## 一、鉴别概览",
        f"",
        f"| 指标 | 数量 |",
        f"|------|------|",
        f"| 鉴别条目总数 | {len(results)} |",
        f"| 🔴 高危 | {high} |",
        f"| 🟡 存疑 | {susp} |",
        f"| ⚪ 需联网检索（离线无法抓取） | {need_online} |",
        f"",
        f"## 二、逐条追溯",
        f"",
        f"| # | 类型 | 输入 | 分级 | 结论 | 命中特征 | 鉴别时间 |",
        f"|---|------|------|------|------|----------|----------|",
    ]
    for i, r in enumerate(results, 1):
        val = r["value"].replace("|", "/")[:60]
        pats = "; ".join(r.get("hit_patterns", []))[:80].replace("|", "/")
        lines.append(f"| {i} | {r['type']} | {val} | {r['risk_level']} | {r.get('verdict','')[:40]} | {pats} | {r.get('verdict_at','')} |")

    lines += [
        f"",
        f"## 三、责任兜底声明",
        f"",
        f"本报告证明：委托机构已通过 Scam Slayer 对上述 {len(results)} 条内容于记录时间完成自动鉴别筛查，",
        f"可作为机构「已履行合理提醒 / 审核义务」的过程证据。",
        f"",
        f"## 四、诚实边界与免责",
        f"",
        f"- 鉴别结论由**规则引擎离线生成，非人工核实**；🔴/🟡 仅为风险提醒，不构成最终定性。",
        f"- 涉及投资、医疗、冒充公检法等重大风险，须以**官方渠道核实结果**为准。",
        f"- 标注「⚪需联网检索」的条目为离线环境无法抓取实时内容，需在有联网能力的会话/自动化中复核。",
        f"- 本报告不替代专业法律/安全意见。",
        f"",
        f"---",
        f"*由 scam-slayer 训练系统（B 端批量接口）自动生成 · 引擎 rule_engine_v4*",
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
