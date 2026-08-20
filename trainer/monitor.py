#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scam-slayer · 持续监控模块 (P0-A · 付费点 A/B 的「主动性」技术底座)
============================================================================
用户/机构提交「盯防账号 / 关键词 / 链接」，定时扫描，命中风险主动产出
「合规可追溯」报告，并可选 webhook 推送（微信/邮件/看板）。

设计要点：
- 复用 auto_verdict.rule_engine_verdict + deep_review_verdict（零 LLM、纯规则引擎 v4），
  不再重写鉴别逻辑。
- 监控数据层放在 trainer/monitor_data/（trainer/ 已被 Coze 分发 zip 排除，不污染已发布 skill 包）。
- 诚实边界（来自 scam-slayer SOP 前车之鉴：B站限流 / 微博0数据 / 海外仅 mock）：
  * url 类 → 做「URL 参数分析」降级鉴别（可离线）。
  * account / keyword 类 → 离线脚本无法自动抓实时内容，标「⚪需联网检索」，
    需在具备 web_search 的对话/自动化中运行。绝不编造鉴别结果。

用法：
  python monitor.py add url <链接> [--label 备注]
  python monitor.py add account <账号名/主页> [--label 备注]
  python monitor.py add keyword <关键词> [--label 备注]
  python monitor.py list
  python monitor.py scan [--type all|url|account|keyword]
  python monitor.py report            # 打印最新一次报告摘要
  python monitor.py report --full     # 打印完整报告
  python monitor.py rm <id>           # 删除盯防项

可选推送：环境变量 MONITOR_WEBHOOK_URL（Server酱/企业微信机器人通用）
"""

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

DATA_DIR = os.path.join(BASE_DIR, "monitor_data")
WATCHLIST_FILE = os.path.join(DATA_DIR, "watchlist.json")
REPORT_DIR = os.path.join(DATA_DIR, "reports")

# 短链 / 可疑跳转域名（URL 探针启发式）
SHORT_LINK_DOMAINS = {
    "dwz.cn", "t.cn", "suo.im", "url.cn", "bit.ly", "dwz.win", "mr.baidu.com",
    "tinyurl.com", "suo.im", "tb.cn", "jd.com", "u.jd.com", "c.tb.cn",
}
# 钓鱼/仿冒高敏词（出现在域名中即视为可疑，需人工核实）
SUSPICIOUS_DOMAIN_HINTS = [
    "login", "verify", "bank", "pay", "activity", "redpacket", "luck", "wx",
    "weixin", "alipay", "taobao", "jd", "security", "safe", "claim", "gift",
    "prize", "中奖", "lk", "vip", "admin",
]

MAX_PER_SCAN = 50  # 单次扫描最多鉴别条数（避免无谓消耗）


# ===================== 数据层 =====================
def _ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(REPORT_DIR, exist_ok=True)


def load_watchlist():
    _ensure_dirs()
    if not os.path.exists(WATCHLIST_FILE):
        return []
    with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_watchlist(data):
    _ensure_dirs()
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _gen_id(value):
    return "M" + hashlib.md5(value.encode("utf-8")).hexdigest()[:8].upper()


# ===================== 增删查 =====================
def add_entry(etype, value, label=""):
    etype = etype.lower()
    if etype not in ("url", "account", "keyword"):
        print(f"❌ 未知类型 {etype}，仅支持 url / account / keyword")
        return
    wl = load_watchlist()
    eid = _gen_id(f"{etype}:{value}")
    if any(e["id"] == eid for e in wl):
        print(f"⚠️  已存在：[{eid}] {etype} {value}")
        return
    wl.append({
        "id": eid,
        "type": etype,
        "value": value,
        "label": label,
        "added_at": datetime.now().isoformat(),
        "last_scan": None,
        "last_result": None,
    })
    save_watchlist(wl)
    print(f"✅ 已加入盯防列表：[{eid}] {etype} → {value}")


def rm_entry(eid):
    wl = load_watchlist()
    new_wl = [e for e in wl if e["id"] != eid.upper()]
    if len(new_wl) == len(wl):
        print(f"⚠️  未找到 {eid}")
        return
    save_watchlist(new_wl)
    print(f"🗑️  已删除 {eid}")


def list_entries():
    wl = load_watchlist()
    if not wl:
        print("📭 盯防列表为空。用 `add` 添加 url / account / keyword。")
        return
    print(f"📋 盯防列表（共 {len(wl)} 条）：")
    for e in wl:
        last = e.get("last_result") or "—"
        print(f"  [{e['id']}] {e['type']:8} | {e['value']}"
              f"  | 上次:{last}  | 备注:{e.get('label','')}")


# ===================== 构造鉴别条目 =====================
def _url_heuristics(url):
    """URL 探针启发式：返回 (额外命中列表, 可疑度说明)"""
    hits = []
    try:
        p = urllib.parse.urlparse(url)
        domain = (p.netloc or "").lower()
        # 去掉 www.
        bare = domain[4:] if domain.startswith("www.") else domain
        # 短链
        if bare in SHORT_LINK_DOMAINS or any(bare.endswith("." + d) for d in SHORT_LINK_DOMAINS):
            hits.append(f"短链/跳转域名「{bare}」（可能隐藏真实去向）")
        # 仿冒高敏词
        for hint in SUSPICIOUS_DOMAIN_HINTS:
            if hint in bare:
                hits.append(f"域名含高敏词「{hint}」（仿冒/钓鱼常见，需核实）")
                break
        # userinfo 钓鱼（http://user@host）
        if p.username or p.password:
            hits.append("URL 含 userinfo（@ 前缀钓鱼手法）")
        # 巨量参数 / 编码
        q = p.query or ""
        if len(q) > 200 or q.count("=") > 8:
            hits.append(f"参数过多({q.count('=')}个)，疑似跟踪/跳转")
        # 非标准端口
        if p.port and p.port not in (80, 443):
            hits.append(f"非标准端口 {p.port}")
        # 参数内含跳转链接到外部域名（钓鱼常用：redirect/target/url/cb 等）
        SAFE_REDIRECT_HOSTS = {"icbc.com.cn", "www.icbc.com.cn"}
        for k, vs in urllib.parse.parse_qs(p.query).items():
            if k.lower() in ("redirect", "target", "url", "to", "cb", "callback", "next", "return"):
                for v in vs:
                    if v.lower().startswith("http"):
                        try:
                            rh = (urllib.parse.urlparse(v).netloc or "").lower()
                            if rh and rh not in SAFE_REDIRECT_HOSTS and rh != bare:
                                hits.append(f"参数「{k}」跳转至外部域名「{rh}」（钓鱼常用）")
                        except Exception:
                            pass
    except Exception:
        pass
    return hits


def build_item(entry):
    """把盯防项构造成 rule_engine_verdict 可消费的 item。
    返回 (item, 来源说明, 是否可离线鉴别)。"""
    etype = entry["type"]
    value = entry["value"]

    if etype == "url":
        heur = _url_heuristics(value)
        p = urllib.parse.urlparse(value)
        domain = (p.netloc or "").lower()
        # 用 URL 本身 + 域名 + 参数 作为可分析文本（降级：未抓取页面内容）
        item = {
            "platform": "url_probe",
            "title": value,
            "summary": f"域名:{domain} 参数:{p.query}",
            "search_keyword": "",
        }
        note = "URL 参数分析（未抓取页面正文，仅按域名/参数/结构判断）"
        # 把 URL 启发式命中写进 summary，让引擎一并计权
        if heur:
            item["summary"] += " | " + " ; ".join(heur)
        return item, note, True, heur

    if etype == "account":
        item = {
            "platform": "account_probe",
            "title": value,
            "account": value,
            "summary": "",
            "search_keyword": "",
        }
        note = "账号名模式分析（仅检账号名高危词，未抓该账号实时内容）"
        return item, note, True, []

    # keyword：离线脚本无法自动检索实时内容
    note = "⚪需联网检索：本离线脚本无法自动抓实时内容，请在具备 web_search 的对话/自动化中运行"
    return None, note, False, []


# ===================== 扫描 =====================
def scan(etype_filter="all"):
    from auto_verdict import rule_engine_verdict, deep_review_verdict

    wl = load_watchlist()
    if etype_filter != "all":
        wl = [e for e in wl if e["type"] == etype_filter.lower()]
    if not wl:
        print("📭 无符合条件的盯防项。")
        return None

    run_at = datetime.now()
    results = []
    scanned = 0
    for e in wl:
        if scanned >= MAX_PER_SCAN:
            break
        item, note, can_verdict, heur = build_item(e)
        rec = {
            "id": e["id"],
            "type": e["type"],
            "value": e["value"],
            "label": e.get("label", ""),
            "scanned_at": run_at.isoformat(),
            "source_note": note,
        }
        if not can_verdict:
            rec["status"] = "⚪需联网检索"
            rec["risk_level"] = "⚪未知"
            rec["verdict"] = note
            rec["hit_patterns"] = []
        else:
            v = rule_engine_verdict(item)
            v = deep_review_verdict(item, v)
            # 合并 URL 启发式命中
            hits = list(v.get("hit_patterns", [])) + heur
            rec["status"] = "✅已鉴别"
            rec["risk_level"] = v.get("risk_level", "🟡存疑")
            rec["verdict"] = v.get("verdict", "")
            rec["hit_patterns"] = hits
            rec["reason"] = v.get("reason", "")
            rec["recommendation"] = v.get("recommendation", "")
            # URL 探针层：结构可疑但未触发引擎高危词时，至少标🟡存疑，
            # 避免「已命中可疑特征却显示可信」的矛盾。强钓鱼信号升🔴。
            if e["type"] == "url" and heur:
                strong = any(("钓鱼" in h) or ("仿冒" in h) or ("userinfo" in h) for h in heur)
                if strong:
                    rec["risk_level"] = "🔴高危"
                    rec["verdict"] = "URL 结构高度可疑（疑似钓鱼/仿冒），需立即人工核实"
                elif "高危" not in rec["risk_level"] and "存疑" not in rec["risk_level"]:
                    rec["risk_level"] = "🟡存疑"
                    rec["verdict"] = "URL 结构存在可疑特征，规则引擎未定论，建议人工核实"
            # 回写盯防项
            e["last_scan"] = run_at.isoformat()
            e["last_result"] = rec["risk_level"]
            scanned += 1
        results.append(rec)

    save_watchlist(load_watchlist_after(wl))  # 持久化 last_scan

    report = _build_report(run_at, results)
    _save_report(report)
    _print_summary(report)
    _notify(report)
    return report


def load_watchlist_after(wl):
    # 仅占位：scan 内已修改 e，直接保存
    return wl


# ===================== 报告（合规可追溯） =====================
def _build_report(run_at, results):
    high = [r for r in results if "高危" in r.get("risk_level", "")]
    susp = [r for r in results if "存疑" in r.get("risk_level", "")]
    unknown = [r for r in results if "未知" in r.get("risk_level", "") or r.get("status", "").startswith("⚪")]
    return {
        "report_type": "scam-slayer 持续监控报告",
        "generated_at": run_at.isoformat(),
        "engine": "rule_engine_v4 (zero-LLM)",
        "total": len(results),
        "high_risk": len(high),
        "suspicious": len(susp),
        "need_online": len(unknown),
        "disclaimer": "本报告由规则引擎离线生成，非人工核实；命中风险仅作提醒，重大决定请向官方渠道核实。",
        "results": results,
    }


def _save_report(report):
    _ensure_dirs()
    ts = report["generated_at"].replace(":", "").replace("-", "").replace("T", "_")[:15]
    path = os.path.join(REPORT_DIR, f"monitor_report_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    report["_path"] = path
    return path


def _print_summary(report):
    print("\n" + "=" * 60)
    print(f"  🛡️  持续监控扫描报告  {report['generated_at']}")
    print(f"  引擎：{report['engine']}")
    print("=" * 60)
    print(f"  总计 {report['total']} 项 | 🔴高危 {report['high_risk']} | "
          f"🟡存疑 {report['suspicious']} | ⚪需联网 {report['need_online']}")
    print("-" * 60)
    for r in report["results"]:
        flag = r["risk_level"]
        print(f"  {flag} [{r['id']}] {r['type']} {r['value'][:48]}")
        if r.get("hit_patterns"):
            print(f"       命中: {', '.join(r['hit_patterns'][:4])}")
        print(f"       {r.get('source_note','')}")
    print("-" * 60)
    print(f"  📄 报告已存: {report.get('_path','')}")
    print(f"  ⚠️  {report['disclaimer']}\n")


def _notify(report):
    url = os.environ.get("MONITOR_WEBHOOK_URL", "").strip()
    if not url:
        return
    # 仅在有风险时推送，避免刷屏
    if report["high_risk"] == 0 and report["suspicious"] == 0:
        print("  🔕 无风险命中，跳过 webhook 推送。")
        return
    payload = {
        "title": "Scam Slayer 监控告警",
        "summary": f"🔴高危{report['high_risk']} / 🟡存疑{report['suspicious']} / 共{report['total']}项",
        "report": report,
    }
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"  📨 webhook 推送成功：HTTP {resp.status}")
    except Exception as ex:
        print(f"  ⚠️  webhook 推送失败：{ex}（报告已本地留存）")


# ===================== 报告查看 =====================
def show_report(full=False):
    if not os.path.exists(REPORT_DIR):
        print("📭 暂无报告。先运行 `scan`。")
        return
    files = sorted(
        [f for f in os.listdir(REPORT_DIR) if f.endswith(".json")],
        reverse=True,
    )
    if not files:
        print("📭 暂无报告。先运行 `scan`。")
        return
    latest = os.path.join(REPORT_DIR, files[0])
    with open(latest, "r", encoding="utf-8") as f:
        report = json.load(f)
    print(f"📄 最新报告：{latest}")
    print(f"   时间 {report['generated_at']} | 引擎 {report['engine']}")
    print(f"   总计 {report['total']} | 🔴{report['high_risk']} 🟡{report['suspicious']} ⚪{report['need_online']}")
    if full:
        for r in report["results"]:
            print("\n---")
            print(f"[{r['id']}] {r['type']} {r['value']}")
            print(f"  分级: {r['risk_level']}")
            print(f"  结论: {r.get('verdict','')}")
            if r.get("hit_patterns"):
                print(f"  命中: {', '.join(r['hit_patterns'])}")
            print(f"  说明: {r.get('source_note','')}")


# ===================== CLI =====================
def main():
    ap = argparse.ArgumentParser(description="scam-slayer 持续监控模块")
    sub = ap.add_subparsers(dest="cmd")

    p_add = sub.add_parser("add")
    p_add.add_argument("type", choices=["url", "account", "keyword"])
    p_add.add_argument("value")
    p_add.add_argument("--label", default="")

    p_rm = sub.add_parser("rm")
    p_rm.add_argument("id")

    sub.add_parser("list")

    p_scan = sub.add_parser("scan")
    p_scan.add_argument("--type", default="all")

    p_rep = sub.add_parser("report")
    p_rep.add_argument("--full", action="store_true")

    args = ap.parse_args()
    cmd = args.cmd

    if cmd == "add":
        add_entry(args.type, args.value, args.label)
    elif cmd == "rm":
        rm_entry(args.id)
    elif cmd == "list":
        list_entries()
    elif cmd == "scan":
        scan(args.type)
    elif cmd == "report":
        show_report(args.full)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
