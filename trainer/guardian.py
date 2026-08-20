#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scam-slayer · 2C 替身守护模块 (P1 · 付费点 B 技术底座)
============================================================================
子女远程绑定父母，系统主动持续监控父母的风险面（由子女代提交的盯防项），
发现风险主动推送告警给子女 + 生成远程看板（markdown）。

设计要点：
- 复用 batch_verdict.verdict_one（与监控/批量同一套鉴别链路，零 LLM 规则引擎 v4）。
- 父母侧零操作：守护的"风险面"由子女在绑定后提交（父母常转发的群/账号/关键词）。
- 告警推送：复用 webhook（MONITOR_WEBHOOK_URL 或绑定级 --webhook）。
- 远程看板：当前为 markdown 生成（Web 看板在 scam-slayer-web 仓库，待开发）。
- 诚实边界：与监控一致——url 做参数分析降级；account/keyword 离线无法抓实时内容标⚪。

用法：
  python guardian.py bind --guardian 子女A --ward 爸爸 [--webhook https://...]
  python guardian.py unbind 子女A 爸爸
  python guardian.py watch 子女A 爸爸 add url https://dwz.cn/xxx --label 爸转的群链接
  python guardian.py watch 子女A 爸爸 add keyword 养老金补发 --label 常搜词
  python guardian.py watch 子女A 爸爸 list
  python guardian.py scan [子女A] [--ward 爸爸]      # 扫描全部守护人风险面，推送告警
  python guardian.py dashboard [子女A] [--ward 爸爸] [--full]
  python guardian.py list                              # 列出所有绑定
"""

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from batch_verdict import verdict_one

BINDINGS_FILE = os.path.join(BASE_DIR, "monitor_data", "guardian_bindings.json")


# ===================== 数据层 =====================
def _ensure():
    os.makedirs(os.path.dirname(BINDINGS_FILE), exist_ok=True)


def load_bindings():
    _ensure()
    if not os.path.exists(BINDINGS_FILE):
        return []
    with open(BINDINGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_bindings(data):
    _ensure()
    with open(BINDINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _find(g, w):
    for b in load_bindings():
        if b["guardian"] == g and b["ward"] == w:
            return b
    return None


# ===================== 绑定 / 盯防 =====================
def bind(guardian, ward, webhook=""):
    bs = load_bindings()
    b = _find(guardian, ward)
    if b:
        if webhook:
            b["webhook"] = webhook
        print(f"⚠️  绑定已存在：{guardian} → {ward}")
    else:
        bs.append({
            "guardian": guardian,
            "ward": ward,
            "webhook": webhook,
            "watchlist": [],
            "created_at": datetime.now().isoformat(),
            "last_scan": None,
        })
        save_bindings(bs)
        print(f"✅ 已绑定守护：{guardian} → {ward}（父母侧零操作）")


def unbind(guardian, ward):
    bs = [b for b in load_bindings() if not (b["guardian"] == guardian and b["ward"] == ward)]
    if len(bs) == len(load_bindings()):
        print(f"⚠️  未找到绑定 {guardian} → {ward}")
        return
    save_bindings(bs)
    print(f"🗑️  已解除绑定 {guardian} → {ward}")


def add_watch(guardian, ward, etype, value, label=""):
    b = _find(guardian, ward)
    if not b:
        print(f"❌ 请先 bind {guardian} {ward}")
        return
    if etype not in ("url", "account", "keyword", "text"):
        print(f"❌ 盯防类型仅支持 url / account / keyword / text（父母转发的链接/账号/常搜词/文案）")
        return
    b["watchlist"].append({
        "type": etype, "value": value, "label": label,
        "added_at": datetime.now().isoformat(),
    })
    save_bindings(load_bindings_after(b, guardian, ward))
    print(f"✅ 已加入 {ward} 的盯防面：[{etype}] {value}")


def load_bindings_after(b, g, w):
    bs = load_bindings()
    for i, x in enumerate(bs):
        if x["guardian"] == g and x["ward"] == w:
            bs[i] = b
    return bs


def list_watch(guardian, ward):
    b = _find(guardian, ward)
    if not b:
        print(f"❌ 未找到绑定 {guardian} → {ward}")
        return
    wl = b.get("watchlist", [])
    if not wl:
        print(f"📭 {ward} 的盯防面为空，用 `watch {guardian} {ward} add ...` 添加。")
        return
    print(f"📋 {ward} 的盯防面（{len(wl)} 项）：")
    for e in wl:
        print(f"  [{e['type']}] {e['value']}  | {e.get('label','')}")


# ===================== 扫描 + 告警 =====================
def _post_webhook(url, payload):
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except Exception as ex:
        print(f"  ⚠️  webhook 推送失败：{ex}")
        return None


def scan(guardian=None, ward=None):
    bs = load_bindings()
    if guardian:
        bs = [b for b in bs if b["guardian"] == guardian]
    if ward:
        bs = [b for b in bs if b["ward"] == ward]
    if not bs:
        print("📭 无匹配的守护绑定。")
        return

    print("\n" + "=" * 60)
    print(f"  👨‍👧 替身守护扫描  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    for b in bs:
        g, w = b["guardian"], b["ward"]
        wl = b.get("watchlist", [])
        print(f"\n👁️  守护对象：{g} → {w}（盯防面 {len(wl)} 项）")
        if not wl:
            print("   （盯防面为空，跳过）")
            continue
        alerts = []
        for e in wl:
            rec = verdict_one({"type": e["type"], "value": e["value"], "label": e.get("label", "")})
            rl = rec.get("risk_level", "")
            flag = "🔴" if "高危" in rl else ("🟡" if "存疑" in rl else ("⚪" if "未知" in rl else "🟢"))
            print(f"  {flag} {e['type']} {e['value'][:44]} | {rec.get('verdict','')[:30]}")
            if "高危" in rl or "存疑" in rl:
                alerts.append({"ward": w, "input": e["value"], "risk_level": rl,
                               "verdict": rec.get("verdict", ""), "hits": rec.get("hit_patterns", [])})
        # 推送告警
        webhook = b.get("webhook") or os.environ.get("MONITOR_WEBHOOK_URL", "")
        if alerts and webhook:
            payload = {
                "title": f"Scam Slayer 替身守护告警 · {w}",
                "alerts": alerts,
                "count": len(alerts),
                "scanned_at": datetime.now().isoformat(),
            }
            st = _post_webhook(webhook, payload)
            print(f"  📨 告警已推送给 {g}（HTTP {st}）" if st else "  📨 告警推送未成功（详见上方⚠️）")
        elif alerts:
            print("  🔕 有风险命中但未配置 webhook（设 --webhook 或 MONITOR_WEBHOOK_URL 以推送）")
        b["last_scan"] = datetime.now().isoformat()
        save_bindings(load_bindings_after(b, g, w))


def dashboard(guardian=None, ward=None, full=False):
    bs = load_bindings()
    if guardian:
        bs = [b for b in bs if b["guardian"] == guardian]
    if ward:
        bs = [b for b in bs if b["ward"] == ward]
    if not bs:
        print("📭 无匹配绑定。")
        return
    print(f"\n# 👨‍👧 替身守护远程看板（{datetime.now().strftime('%Y-%m-%d %H:%M')}）")
    print("> 注：当前为本地 markdown 看板；Web 看板在 scam-slayer-web 仓库，待开发。\n")
    for b in bs:
        g, w = b["guardian"], b["ward"]
        wl = b.get("watchlist", [])
        print(f"## 守护：{g} → {w}")
        print(f"- 盯防面条目：{len(wl)} | 上次扫描：{b.get('last_scan') or '未扫描'}")
        if not wl:
            print("- （暂无盯防项）\n")
            continue
        # 实时重算概览
        risk_counts = {"🔴高危": 0, "🟡存疑": 0, "🟢可信": 0, "⚪需联网": 0}
        details = []
        for e in wl:
            rec = verdict_one({"type": e["type"], "value": e["value"], "label": e.get("label", "")})
            rl = rec.get("risk_level", "")
            if "高危" in rl: risk_counts["🔴高危"] += 1
            elif "存疑" in rl: risk_counts["🟡存疑"] += 1
            elif "未知" in rl: risk_counts["⚪需联网"] += 1
            else: risk_counts["🟢可信"] += 1
            details.append((e, rec))
        print(f"- 风险概览：🔴{risk_counts['🔴高危']} 🟡{risk_counts['🟡存疑']} "
              f"🟢{risk_counts['🟢可信']} ⚪{risk_counts['⚪需联网']}")
        if full:
            for e, rec in details:
                if "可信" in rec.get("risk_level", "") and "未知" not in rec.get("risk_level", ""):
                    continue
                print(f"  - {rec.get('risk_level','')} {e['type']} {e['value'][:50]}")
                if rec.get("hit_patterns"):
                    print(f"    命中: {', '.join(rec['hit_patterns'][:4])}")
        print()


def list_bindings():
    bs = load_bindings()
    if not bs:
        print("📭 暂无守护绑定。用 `bind --guardian <你> --ward <父母>` 创建。")
        return
    print(f"📋 守护绑定（{len(bs)} 条）：")
    for b in bs:
        print(f"  {b['guardian']} → {b['ward']} | 盯防面 {len(b.get('watchlist', []))} 项 | webhook: {'✅' if b.get('webhook') else '未设'}")


# ===================== CLI =====================
def main():
    ap = argparse.ArgumentParser(description="scam-slayer 替身守护模块")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("bind")
    p.add_argument("--guardian", required=True)
    p.add_argument("--ward", required=True)
    p.add_argument("--webhook", default="")

    p = sub.add_parser("unbind")
    p.add_argument("guardian")
    p.add_argument("ward")

    p = sub.add_parser("watch")
    p.add_argument("guardian")
    p.add_argument("ward")
    p.add_argument("action", choices=["add", "list"])
    p.add_argument("type", nargs="?", default="")
    p.add_argument("value", nargs="?", default="")
    p.add_argument("--label", default="")

    p = sub.add_parser("scan")
    p.add_argument("guardian", nargs="?")
    p.add_argument("--ward", default=None)

    p = sub.add_parser("dashboard")
    p.add_argument("guardian", nargs="?")
    p.add_argument("--ward", default=None)
    p.add_argument("--full", action="store_true")

    sub.add_parser("list")

    args = ap.parse_args()
    cmd = args.cmd
    if cmd == "bind":
        bind(args.guardian, args.ward, args.webhook)
    elif cmd == "unbind":
        unbind(args.guardian, args.ward)
    elif cmd == "watch":
        if args.action == "add":
            add_watch(args.guardian, args.ward, args.type, args.value, args.label)
        else:
            list_watch(args.guardian, args.ward)
    elif cmd == "scan":
        scan(args.guardian, args.ward)
    elif cmd == "dashboard":
        dashboard(args.guardian, args.ward, args.full)
    elif cmd == "list":
        list_bindings()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
