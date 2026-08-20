#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scam-Slayer · 两端知识库同步（web 端 -> skill 端）
================================================
问题：每日自动化采集只写到 web 端 scam-slayer-data/data/，skill 端 references/
实际运行时读的是几周前的旧库（patterns.md 停在 7/21、基础指南停在 3/31），
cache/ 虽有较新副本但没接进运行时。导致 skill 端跑旧套路库。

本脚本把 web 端作为单一事实源，把「共享文件」同步进 skill 端两处目录：
  - references/  : skill 运行时真正读的数据目录
  - cache/       : skill 端每日采集缓存目录（保持新鲜）

保护规则：
  - 只覆盖共享文件，绝不删除 skill 端独有文件（truth-base.json）。注：rumor-library.md 现已并入 SHARED_MD，由 web 端为准同步进 skill 端。
  - 若某共享文件 web 端不存在，则跳过该文件（不制造空文件）。

用法：
  python sync_kb.py            # 执行同步
  python sync_kb.py --dry      # 只打印将要做什么，不写文件
"""
import os
import sys
import shutil
import glob
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_KB = os.path.abspath(os.path.join(HERE, "..", "data"))
SKILL_DIR = os.path.expanduser("~/.workbuddy/skills/scam-slayer")
SKILL_REFS = os.path.join(SKILL_DIR, "references")
SKILL_CACHE = os.path.join(SKILL_DIR, "cache")

# 共享 .md（web 与 skill 都有，以 web 为准）
SHARED_MD = [
    "patterns.md",
    "case-library.md",
    "wechat-patterns.md",
    "phone-scam.md",
    "elderly-guide.md",
    "rumor-library.md",
]

# skill 端独有、绝不触碰
SKILL_ONLY = {"truth-base.json"}

# 每日采集产物（仅进 cache/，不进 references/ 以免污染发布结构）
# 注意实际命名：2026-08-11-collected-cases.md（日期在前），不是 collected-cases-*.md
DATE_GLOB_FILES = ["*-collected-cases.md", "*-promoted.md", "training_report.md"]


def _mtime(p):
    try:
        return datetime.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%m/%d %H:%M")
    except Exception:
        return "—"


def sync(dry=False):
    if not os.path.isdir(WEB_KB):
        print(f"[sync] ✗ web KB 不存在：{WEB_KB}")
        return 1
    if not os.path.isdir(SKILL_REFS):
        print(f"[sync] ✗ skill references 不存在：{SKILL_REFS}")
        return 1
    os.makedirs(SKILL_CACHE, exist_ok=True)

    copied = []
    skipped = []

    # 1) 共享 .md -> references/ 与 cache/
    for name in SHARED_MD:
        src = os.path.join(WEB_KB, name)
        if not os.path.exists(src):
            skipped.append(f"{name}（web 端无，跳过）")
            continue
        for dst_dir in (SKILL_REFS, SKILL_CACHE):
            dst = os.path.join(dst_dir, name)
            if dry:
                copied.append(f"{name} -> {dst_dir}/  [{_mtime(src)}]")
            else:
                shutil.copy2(src, dst)
                copied.append(f"{name} -> {dst_dir}/  [{_mtime(src)}]")

    # 2) 每日采集产物 -> cache/ 仅
    for pat in DATE_GLOB_FILES:
        for src in sorted(glob.glob(os.path.join(WEB_KB, pat))):
            name = os.path.basename(src)
            dst = os.path.join(SKILL_CACHE, name)
            if dry:
                copied.append(f"{name} -> cache/  [{_mtime(src)}]")
            else:
                shutil.copy2(src, dst)
                copied.append(f"{name} -> cache/  [{_mtime(src)}]")

    # 3) 保护确认：skill-only 文件仍在
    for name in SKILL_ONLY:
        p = os.path.join(SKILL_REFS, name)
        status = _mtime(p) if os.path.exists(p) else "缺失!"
        skipped.append(f"{name} 保留 [{status}]")

    print(f"[sync] {'演练' if dry else '执行'}完成：复制 {len(copied)} 项，保留 {len(skipped)} 项")
    for c in copied:
        print(f"  + {c}")
    for s in skipped:
        print(f"  = {s}")
    return 0


if __name__ == "__main__":
    dry = "--dry" in sys.argv
    sys.exit(sync(dry=dry))
