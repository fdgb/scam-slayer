# -*- coding: utf-8 -*-
"""给 data/patterns.md 的 111 条套路补结构化维度标签（受害人群 / 场景 / 渠道）。

用法：python3 scripts/tag_dimensions.py
  - 读取 ../data/patterns.md，对每个 `## ` 二级标题（111 条）注入一行
    `> **【维度标签】** 人群：… ｜ 场景：… ｜ 渠道：…`
  - 先写 patterns.md.bak 备份，再原地覆盖。
  - 打标为规则（关键词命中），属首遍自动标注，需人工复核微调。

依赖：同目录 dimension_tags.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dimension_tags import tag_text, fmt_tags  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.normpath(os.path.join(HERE, "..", "data", "patterns.md"))


def split_sections(lines):
    """把文本拆成 (preamble_lines, [(heading, body_lines), ...])。"""
    preamble = []
    sections = []
    cur_head = None
    cur_body = []
    in_preamble = True
    for line in lines:
        if line.startswith("## ") and not line.startswith("### "):
            if cur_head is not None:
                sections.append((cur_head, cur_body))
            cur_head = line
            cur_body = []
            in_preamble = False
        else:
            if in_preamble:
                preamble.append(line)
            else:
                cur_body.append(line)
    if cur_head is not None:
        sections.append((cur_head, cur_body))
    return preamble, sections


def main():
    if not os.path.exists(SRC):
        print(f"未找到 {SRC}")
        return
    lines = open(SRC, encoding="utf-8").read().split("\n")
    preamble, sections = split_sections(lines)

    # 备份
    bak = SRC + ".bak"
    open(bak, "w", encoding="utf-8").write("\n".join(lines))

    res = list(preamble)
    for head, body in sections:
        text = head + "\n" + "\n".join(body)
        pops, scns, chns = tag_text(text)
        res.append(head)
        res.append("> **【维度标签】** " + fmt_tags(pops, scns, chns))
        res.extend(body)

    open(SRC, "w", encoding="utf-8").write("\n".join(res))

    print(f"已标注 {len(sections)} 个套路；备份={bak}")
    print("===== 前 3 条样例（请人工核对标签质量）=====")
    for head, body in sections[:3]:
        print("\n" + head)
        print("> **【维度标签】** " + fmt_tags(*tag_text(head + "\n" + "\n".join(body))))


if __name__ == "__main__":
    main()
