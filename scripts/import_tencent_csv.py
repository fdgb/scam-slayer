#!/usr/bin/env python3
"""
import_tencent_csv.py — 把腾讯问卷收集表的 CSV 导出，半自动搬进待审队列。

用法：
    python3 scripts/import_tencent_csv.py [CSV路径] [--dry-run]

默认 CSV 路径：仓库根目录的 survey_submissions.csv
（从腾讯问卷 → 分析 → 数据 → 导出 CSV 得到）

它会：
  1. 读取 CSV（utf-8-sig，兼容腾讯文档 BOM）；
  2. 按表头把行映射到标准样本字段（支持中英文表头别名）；
  3. 质量门：
     - 过短(<10字)直接跳过；
     - 与 data/*.md 已有内容做 Jaccard 行级去重，相似度>0.85 判为重复，跳过并提示；
     - 扫描高风险断言（事实核查标记）；
  4. 把通过的样本追加进 pending_samples.md（来源标注「腾讯文档收集表」），
     周一自动化会照常读取并入库；
  5. 打印导入摘要。

注意：本脚本只做「归集 + 去重 + 标记」，不做人工脱敏。
真正入库前的脱敏与裁定仍由作者（周一自动化 / 周六复盘）完成。
"""
import csv
import os
import re
import sys
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
PENDING = os.path.join(ROOT, "pending_samples.md")
DEFAULT_CSV = os.path.join(ROOT, "survey_submissions.csv")

# 表头别名映射：规范字段名 -> 可能的 CSV 表头（中文/英文）
# 同时兼容「实际收集表表头」「旧版文档表头」，任一出现都能识别。
# 注：腾讯问卷导出的 CSV 列名=题目原文（带 "1." "2." 编号前缀），故下表首项是真实导出值。
# 若首次导入报"缺少内容列"，请把导出的 CSV 表头行发我，我对齐后重跑。
HEADER_MAP = {
    "submit_time": ["开始答题时间", "提交时间", "提交时间（自动）", "timestamp", "时间", "填写时间"],
    "type": ["2.内容类型：你遇到的是哪种内容？", "内容类型", "你遇到的是哪种内容？", "type", "类型"],
    "platform": ["3.来源平台：这段内容来自哪里？", "来源平台", "这段内容来自哪里？", "platform", "平台"],
    "content": ["1.提交内容：请把你看到的可疑内容原文或摘要贴在这里", "提交内容", "可疑内容原文或摘要", "内容", "content", "原文", "描述"],
    # reason 在问卷里合并进了「补充信息」第 6 题，故两个字段都指向该列
    "reason": ["你怀疑它是骗局的原因", "怀疑原因", "reason", "原因",
               "6.补充信息：你怀疑它是骗局的原因、上下文、或想补充说明的都写这里"],
    "target": ["涉及对象", "对象", "target", "4.涉及对象"],
    "judgment": ["5.你的初步判断：你觉得它是真的还是假的？", "你的初步判断", "初步判断", "judgment"],
    "lost_money": ["是否已经上当/转账", "是否转账", "lost_money", "转账"],
    "note": ["6.补充信息：你怀疑它是骗局的原因、上下文、或想补充说明的都写这里", "补充信息", "补充说明", "note", "备注", "其他"],
    # 联系方式为可选隐私字段（问卷已删），脚本即使检测到也不写入公开知识库（见 build_entry）
    "contact": ["联系方式", "contact", "电话", "微信"],
}

# 事实核查高风险模式（与 build_manifest.py 保持一致）
FACT_CHECK_PATTERNS = [
    r"专家?[说称]|权威[人士机构]?",
    r"最新[研究|发现|报告]",
    r"据.*?统计|数据显示",
    r"已[经确诊|证实|官方确认]",
    r"央视[报道|曝光]|人民日报",
    r"国家[部委局]发布",
    r"[院士|教授|医生|专家]\s+\S{2,8}(说|称|表示)",
    r"治愈率\d+|%?\s*(以上|左右)?",
    r"\d+(\.\d+)?\s*万?人?(感染|死亡|确诊|中招)",
]


def map_headers(headers):
    """返回 {规范字段名: csv列索引}。找不到的列返回 None。

    匹配策略两遍：
      1) 精确匹配（忽略大小写、首尾空格）
      2) 子串包含（任一方向）：兼容腾讯问卷带 "1." 编号前缀、或题面微调
    """
    norm = {}
    for field, aliases in HEADER_MAP.items():
        idx = None
        # Pass 1: 精确匹配
        for i, h in enumerate(headers):
            h_norm = h.strip().lower()
            if h_norm in [a.lower() for a in aliases]:
                idx = i
                break
        # Pass 2: 子串包含（任一方向）
        if idx is None:
            for i, h in enumerate(headers):
                h_norm = h.strip().lower()
                for a in aliases:
                    a_norm = a.lower().strip()
                    if a_norm and (a_norm in h_norm or h_norm in a_norm):
                        idx = i
                        break
                if idx is not None:
                    break
        norm[field] = idx
    return norm


def jaccard_lines(a: str, b: str) -> float:
    sa = set(a.splitlines())
    sb = set(b.splitlines())
    if not sa and not sb:
        return 1.0
    union = len(sa | sb)
    return len(sa & sb) / union if union else 0.0


def load_corpus():
    """读取 data/*.md 作为去重比对语料，返回 list[(name, text)]。"""
    corpus = []
    if not os.path.isdir(DATA_DIR):
        return corpus
    for name in sorted(os.listdir(DATA_DIR)):
        p = os.path.join(DATA_DIR, name)
        if os.path.isfile(p) and name.endswith(".md"):
            try:
                corpus.append((name, open(p, encoding="utf-8").read()))
            except Exception:
                pass
    return corpus


def fact_check_flags(text: str) -> list[str]:
    hits = []
    for pat in FACT_CHECK_PATTERNS:
        if re.search(pat, text):
            hits.append(pat)
    return hits


def build_entry(row, col, corpus):
    """把一行 CSV 转成标准样本 markdown 块。返回 dict 或 None（跳过）。"""
    def get(field):
        i = col.get(field)
        if i is None or i >= len(row):
            return ""
        return (row[i] or "").strip()

    # 选择题答案常带 "E." "B." 字母前缀，清掉只留内容
    def clean_choice(val):
        return re.sub(r"^[A-Za-z][\.、]\s*", "", val).strip()

    content = get("content")
    if len(content) < 10:
        return None  # 过短跳过

    ctype = clean_choice(get("type")) or "未指定"
    platform = clean_choice(get("platform")) or "未指定"
    reason = clean_choice(get("reason"))
    target = get("target")
    judgment = clean_choice(get("judgment"))
    lost = get("lost_money")
    note = get("note")
    # contact = get("contact")  # 隐私字段，不写入公开知识库
    stime = get("submit_time") or datetime.date.today().strftime("%Y-%m-%d")

    # 去重
    new_text = f"{ctype}\n{platform}\n{content}\n{reason}\n{target}\n{judgment}"
    max_sim = 0.0
    for _, text in corpus:
        sim = jaccard_lines(new_text, text)
        if sim > max_sim:
            max_sim = sim
    if max_sim > 0.85:
        return {"skip": "duplicate", "similarity": max_sim, "content": content[:30]}

    # 事实核查
    fact_hits = fact_check_flags(content + " " + (reason or ""))

    body = [
        "## 样本信息",
        f"- **内容类型**：{ctype}",
        f"- **来源平台**：{platform}",
        f"- **原始内容/摘要**：{content}",
    ]
    if reason:
        body.append(f"- **用户怀疑的点**：{reason}")
    if target:
        body.append(f"- **涉及对象**：{target}")
    if judgment:
        body.append(f"- **用户初步判断**：{judgment}")
    if lost:
        body.append(f"- **是否已转账**：{lost}")
    if note:
        body.append(f"- **补充说明**：{note}")
    body.append(f"- **提交时间**：{stime}")
    body.append("- **是否已核实**：否（待作者核实）")
    body.append("- **来源**：腾讯问卷收集表")
    body.append("- **隐私说明**：提交者填写的「联系方式」出于隐私不写入本知识库")
    if fact_hits:
        body.append(f"- **⚠️ 事实核查标记**：{len(fact_hits)} 处高风险断言，入库前请联网核实")
    body.append("")

    return {"md": "\n".join(body), "fact": bool(fact_hits)}


def main() -> None:
    csv_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CSV
    dry_run = "--dry-run" in sys.argv
    if not os.path.exists(csv_path):
        raise SystemExit(f"找不到 CSV: {csv_path}\n请先从腾讯问卷收集表导出 CSV 放到该路径（或作为参数传入）。")

    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        raise SystemExit("CSV 为空。")
    headers = rows[0]
    data_rows = rows[1:]

    col = map_headers(headers)
    if col["content"] is None:
        raise SystemExit("CSV 缺少「内容」列，无法导入。请确认收集表包含「可疑内容原文或摘要」字段。")

    corpus = load_corpus()
    imported, skipped_short, skipped_dup, flagged = 0, 0, 0, 0
    blocks = []

    for row in data_rows:
        if not any(c.strip() for c in row):
            continue  # 空行
        res = build_entry(row, col, corpus)
        if res is None:
            skipped_short += 1
            continue
        if res.get("skip") == "duplicate":
            skipped_dup += 1
            print(f"  ↺ 跳过疑似重复 (相似度 {res['similarity']:.0%}): {res['content']}...")
            continue
        blocks.append(res["md"])
        imported += 1
        if res["fact"]:
            flagged += 1

    if not dry_run and blocks:
        with open(PENDING, encoding="utf-8") as f:
            existing = f.read()
        header_sep = "\n---\n"
        new_block = header_sep + "\n".join(blocks)
        with open(PENDING, "w", encoding="utf-8") as f:
            f.write(existing.rstrip() + new_block + "\n")
        print(f"已追加 {imported} 条到 pending_samples.md")

    print(f"\n=== 导入摘要 ===")
    print(f"读取行数     : {len(data_rows)}")
    print(f"成功导入     : {imported}")
    print(f"过短跳过     : {skipped_short}")
    print(f"重复跳过     : {skipped_dup}")
    print(f"需事实核查   : {flagged}")
    if dry_run:
        print("（dry-run，未写入文件）")


if __name__ == "__main__":
    main()
