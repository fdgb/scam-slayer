# -*- coding: utf-8 -*-
"""半自动新话术发现（解决"时效"痛点：新套路靠人工）。

思路：每日采集的待审核样本（pending_samples.md）里藏着尚未写入 patterns.md
的新话术。本脚本把它们「类型」拆词，与现有 111 类套路比对，未命中者标为
潜在新话术，产出一份**草稿**供人工复核后入库——不做自动写入（避免误判污染 KB）。

用法：python3 scripts/discover_patterns.py
产出：../pattern_proposals.md（草稿，待人工复核）

依赖：同目录 dimension_tags.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dimension_tags import tag_text, fmt_tags  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.normpath(os.path.join(HERE, ".."))
PENDING = os.path.join(DATA, "pending_samples.md")
PATTERNS = os.path.join(DATA, "data", "patterns.md")
OUT = os.path.join(DATA, "pattern_proposals.md")


def load_patterns_text():
    if not os.path.exists(PATTERNS):
        return ""
    return open(PATTERNS, encoding="utf-8").read()


def parse_pends(text):
    """按 '## [待审核] PEND-...' 切分待审核块，返回 [(pid, body), ...]。"""
    blocks = re.split(r"(?m)^##\s+\[待审核\]\s+(PEND-[^\n]+)", text)
    res = []
    for i in range(1, len(blocks), 2):
        pid = blocks[i]
        body = blocks[i + 1] if i + 1 < len(blocks) else ""
        res.append((pid, body))
    return res


def fragments(body):
    """抽取「类型：…」行并拆词。

    先用常规分隔符（/ 、 ，,）切，再对每个片段按 （ 【 ； 进一步切取前缀，
    并丢弃过长（>12 字，多为整句 runoff）或过短（<2 字）的片段，避免把整条
    类型描述误判为一个"新话术词"。
    """
    m = re.search(r"类型[：:]\s*([^\n]+)", body)
    if not m:
        return []
    line = m.group(1)
    raw = re.split(r"[／/、,，；（(【]", line)
    parts = []
    for p in raw:
        p = re.split(r"[）)】]", p)[0].strip()
        if 2 <= len(p) <= 12:
            parts.append(p)
    return parts


def main():
    if not os.path.exists(PENDING):
        print("无 pending_samples.md，跳过（先跑每日采集才有样本）")
        return
    ptext = open(PENDING, encoding="utf-8").read()
    patterns_text = load_patterns_text()
    pends = parse_pends(ptext)

    proposals = []
    for pid, body in pends:
        frags = fragments(body)
        new = [f for f in frags if len(f) >= 2 and f not in patterns_text]
        if new:
            pops, scns, chns = tag_text(body)
            proposals.append((pid, new, fmt_tags(pops, scns, chns, cap=6), body.strip()[:160]))

    lines = [
        "# 新话术发现草稿（半自动 · 待人工复核）",
        "",
        "> 由 `scripts/discover_patterns.py` 生成：扫描 `pending_samples.md` 中待审核样本，",
        "> 将其「类型」拆词后与 `data/patterns.md`（111 类套路）比对，未命中现有套路词即标记为潜在新话术。",
        "> **仅为草稿**，须人工核实真实性、补「典型表现 / 判定依据」后，才写入 patterns.md / case-library.md。",
        "> 禁止脚本自动入库（避免误判污染知识库）。",
        "",
    ]
    if not proposals:
        lines.append("（本次未发现明显未覆盖的新话术词；或样本类型均已被现有 111 类套路覆盖）")
    else:
        for pid, new, tags, snippet in proposals:
            lines.append(f"## 候选 {pid}")
            lines.append(f"- **疑似新话术词**：{'、'.join(new)}")
            lines.append(f"- **建议维度标签**：{tags}")
            lines.append(f"- **来源摘要**：{snippet}")
            lines.append("")

    open(OUT, "w", encoding="utf-8").write("\n".join(lines))
    print(f"扫描 {len(pends)} 条待审核样本，产出 {len(proposals)} 条候选草稿 → {OUT}")


if __name__ == "__main__":
    main()
