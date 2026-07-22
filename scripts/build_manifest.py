#!/usr/bin/env python3
"""
build_manifest.py — 生成 version.json 数据清单 + 质量门检查。

每周（或每次更新 data/ 后）运行一次：
    python3 scripts/build_manifest.py

它会：
  1. 扫描 data/ 下所有 .md 文件，计算 sha256 + 体积；
  2. 【质量门】对每个文件执行：
     - 去重检测：与上一版本比对，标记新增/修改/未变的内容块；
     - 格式校验：检查关键文件是否包含必要结构；
     - 事实核查标记：扫描是否含需联网核实的高风险断言；
  3. 按当天日期生成语义化版本号（vYYYY.MM.DD.N）；
  4. 写出根目录 version.json + quality_report.json；
  5. 输出质量摘要到 stdout。

质量门不阻断构建——它只做标记和警告。最终裁定由作者（周一自动化+周六复盘会）决定。
"""
import hashlib
import json
import os
import re
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "version.json")
QUALITY_OUT = os.path.join(ROOT, "quality_report.json")

# ---- 质量规则配置 ----

# 需要事实核查的高风险关键词模式（命中即标记 needs-fact-check）
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

# 每个数据文件的必要结构（至少应包含的 markdown 标题）
REQUIRED_HEADERS = {
    "patterns.md": ["##", "话术", "模式"],
    "case-library.md": ["##", "CASE-", "案例"],
    "elderly-guide.md": ["##", "适老", "老人"],
    "phone-scam.md": ["##", "电话", "诈骗"],
    "wechat-patterns.md": ["##", "微信", "群"],
}


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def read_file(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def check_format(name: str, content: str) -> list[str]:
    """检查文件格式是否符合基本结构要求。返回 warning 列表。"""
    warnings = []
    req = REQUIRED_HEADERS.get(name)
    if req:
        for keyword in req:
            if keyword not in content:
                warnings.append(f"缺少预期结构关键词: {keyword}")
    # 通用检查：文件不能为空或过短
    if len(content.strip()) < 50:
        warnings.append("内容过短(<50字符)，可能不完整")
    return warnings


def check_fact_risk(content: str) -> list[dict]:
    """扫描内容中的高风险断言。返回命中的位置列表。"""
    hits = []
    for pattern in FACT_CHECK_PATTERNS:
        for m in re.finditer(pattern, content):
            # 取匹配处前后各 30 字符作为上下文
            start = max(0, m.start() - 30)
            end = min(len(content), m.end() + 30)
            context = content[start:end].replace("\n", " ")
            hits.append({
                "pattern": pattern,
                "context": context,
                "position": m.start(),
            })
    return hits


def dedup_check(new_content: str, old_content: str | None, name: str) -> dict:
    """
    与旧版本做去重比对。
    返回: {"status": "new"|"modified"|"unchanged", "similarity": float, "notes": list}
    """
    if old_content is None:
        return {"status": "new", "similarity": 0.0, "notes": ["全新文件"]}

    if new_content == old_content:
        return {"status": "unchanged", "similarity": 1.0, "notes": ["无变化"]}

    # 简单相似度：按行计算 Jaccard 相似系数
    new_lines = set(new_content.splitlines())
    old_lines = set(old_content.splitlines())
    intersection = len(new_lines & old_lines)
    union = len(new_lines | old_lines)
    similarity = intersection / union if union > 0 else 0.0

    notes = []
    if similarity > 0.9:
        notes.append(f"高度相似(>90%)，可能是小改")
    elif similarity > 0.7:
        notes.append("较大部分重叠(70-90%)，注意是否有重复条目")
    elif similarity < 0.3:
        notes.append("大幅改动(<30%重叠)，属于重大更新")

    return {
        "status": "modified",
        "similarity": round(similarity, 4),
        "notes": notes,
    }


def run_quality_gate(files_info: dict, prev_version: dict | None) -> dict:
    """对所有数据文件执行质量门检查。返回质量报告。"""
    report = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "files": {},
        "summary": {
            "total_files": len(files_info),
            "passed": 0,
            "warnings": 0,
            "fact_check_needed": 0,
            "duplicates_flagged": 0,
        },
    }

    for name, meta in files_info.items():
        path = os.path.join(DATA_DIR, name)
        content = read_file(path)

        file_report = {}

        # 格式检查
        format_warnings = check_format(name, content)
        file_report["format_warnings"] = format_warnings
        report["summary"]["warnings"] += len(format_warnings)

        # 事实核查风险扫描
        fact_hits = check_fact_risk(content)
        file_report["fact_check_flags"] = [
            {k: v for k, v in hit.items() if k != "position"}
            for hit in fact_hits
        ]
        if fact_hits:
            report["summary"]["fact_check_needed"] += 1
            file_report["needs_fact_check"] = True
        else:
            file_report["needs_fact_check"] = False

        # 去重比对（与上一版本）
        old_content = None
        if prev_version and name in prev_version.get("files", {}):
            try:
                old_content = read_file(os.path.join(DATA_DIR, name))
            except Exception:
                pass

        dup_result = dedup_check(content, old_content, name)
        file_report["dedup"] = {
            "status": dup_result["status"],
            "similarity": dup_result["similarity"],
            "notes": dup_result["notes"],
        }
        if dup_result["similarity"] > 0.85 and dup_result["status"] != "unchanged":
            report["summary"]["duplicates_flagged"] += 1
            file_report["potential_duplicate"] = True
        else:
            file_report["potential_duplicate"] = False

        # 综合判定
        file_report["quality_status"] = (
            "PASS"
            if not format_warnings and not fact_hits
            else ("WARN" if format_warnings else "REVIEW")
        )
        if file_report["quality_status"] == "PASS":
            report["summary"]["passed"] += 1

        report["files"][name] = file_report

    return report


def main() -> None:
    if not os.path.isdir(DATA_DIR):
        raise SystemExit(f"data/ 目录不存在: {DATA_DIR}")

    files = {}
    for name in sorted(os.listdir(DATA_DIR)):
        p = os.path.join(DATA_DIR, name)
        if os.path.isfile(p) and name.endswith(".md"):
            files[name] = {"sha256": sha256(p), "bytes": os.path.getsize(p)}

    if not files:
        raise SystemExit("data/ 下没有任何 .md 文件，终止。")

    today = datetime.date.today().strftime("%Y.%m.%d")
    version = f"v{today}.1"

    # 读上一版 version.json 用于去重比对
    prev_version = None
    if os.path.exists(OUT):
        try:
            prev_version = json.load(open(OUT, encoding="utf-8"))
            if prev_version.get("version", "").startswith(f"v{today}"):
                patch = int(prev_version["version"].split(".")[-1]) + 1
                version = f"v{today}.{patch}"
        except Exception:
            pass

    # ====== 质量门 ======
    quality = run_quality_gate(files, prev_version)

    manifest = {
        "version": version,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "file_count": len(files),
        "files": files,
        "changelog": "auto-built with quality gate",
        "quality_gate": {
            "status": (
                "ALL_PASS"
                if quality["summary"]["warnings"] == 0 and quality["summary"]["fact_check_needed"] == 0
                else "NEEDS_REVIEW"
            ),
            "passed": quality["summary"]["passed"],
            "warnings": quality["summary"]["warnings"],
            "fact_check_needed": quality["summary"]["fact_check_needed"],
            "duplicates_flagged": quality["summary"]["duplicates_flagged"],
        },
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    with open(QUALITY_OUT, "w", encoding="utf-8") as f:
        json.dump(quality, f, ensure_ascii=False, indent=2)

    print(f"wrote {OUT}")
    print(f"wrote {QUALITY_OUT}")
    print(f"version   : {version}")
    print(f"files     : {len(files)}")

    # 质量摘要输出
    s = quality["summary"]
    gate_status = manifest["quality_gate"]["status"]
    emoji = "✅" if gate_status == "ALL_PASS" else "⚠️"
    print(f"{emoji} 质量: {gate_status} | "
          f"通过={s['passed']} | "
          f"格式警告={s['warnings']} | "
          f"需核查={s['fact_check_needed']} | "
          f"疑似重复={s['duplicates_flagged']}")

    if s["fact_check_needed"]:
        print("  → 以下文件含高风险断言，建议联网核查:")
        for name, fr in quality["files"].items():
            if fr.get("needs_fact_check"):
                print(f"     • {name}: {len(fr['fact_check_flags'])} 处标记")
    if s["duplicates_flagged"]:
        print("  → 以下文件与上版高度相似(>85%)，请确认非重复:")
        for name, fr in quality["files"].items():
            if fr.get("potential_duplicate"):
                print(f"     • {name}: 相似度 {fr['dedup']['similarity']:.0%}")


if __name__ == "__main__":
    main()
