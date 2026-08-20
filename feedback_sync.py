#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scam-Slayer · 可选跨用户反馈同步（训练飞轮回传）
============================================================
⚠️ 诚实红线：本模块**默认零收集**。只有用户显式 `python feedback_sync.py --opt-in`
之后，才会在每次「鉴定确认 / 判分 / 校正」后，把**脱敏**贡献匿名回传到你的收集端点。

回传内容（刻意不含任何身份 / 不含原始链接/账号/截图原文）：
  - verdict   ：用户对鉴别结论的判分（correct/partial/wrong）+ 风险类型 + 分级 + 命中模式标签 + **内容 SHA256（单向不可逆，原始内容不留服务器）**
  - confirm   ：用户确认「归入SOP」时，发模式签名（风险类型+分级+命中模式标签+内容 hash），不含具体 URL/账号/原文
  - correction：用户说「不对/改」时，发校正信号（原分级→应分级+内容 hash）
  - usage     ：匿名统计（提交类型分布），用于优先级，不含内容
绝不回传：原始链接 / 账号名 / 截图或文本原文 / 用户身份 / 对话内容。

设计要点（复用 musicmood 飞轮模板）：
  - 纯标准库（urllib/uuid/json/hashlib），零依赖，关闭时完全离线。
  - `_send` 非阻塞、超时 5s、吞掉一切异常，**绝不**影响鉴定主流程。
  - 收集端点由你自己部署（多租户 collector，skill_id=scam-slayer 隔离），默认占位符不会真发到别处。
  - 原始内容只在本地计算 SHA256，hash 单向不可逆，服务器只存 hash + 聚合计数。

用法：
  python feedback_sync.py --opt-in            # 开启匿名反馈同步
  python feedback_sync.py --opt-out           # 关闭，并确认不再收集
  python feedback_sync.py --status            # 查看开关 / 匿名 id / 端点 / 待发队列
  python feedback_sync.py --flush             # 立即把本地待回传的贡献推一次
"""
import os, sys, json, uuid, datetime, hashlib, urllib.request, urllib.error, re

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.expanduser("~/.scam-slayer/feedback_opt_in.json")
OUTBOX = os.path.expanduser("~/.scam-slayer/feedback_outbox.jsonl")  # 发送失败时的本地待发队列（断网/重启时暂存，零丢失）
# 默认回传端点（腾讯云 Lighthouse 固定公网 IP 直连；与 musicmood 同机但**独立端口 8081**，
# 进程/防火墙/数据完全隔离，不动你已上线的 musicmood 收集端 8080）。
DEFAULT_ENDPOINT = "http://140.143.85.18:8081/v1/contribute"

SKILL_ID = "scam-slayer"
# 客户端共享密钥头常量：自动随每次回传带上，配合收集端 SCAM_SLAYER_KEY 校验挡掉路人灌数据。
# 局限：写在分发源码里可见，仅防「知道 URL 但无 zip」的路人；要更强需服务端签发 token（超纲）。
SHARED_KEY = "ss-shared-4Mp7rT2vK8nX5hQz"
SCHEMA_VERSION = 1

# 归一化时剔除的 URL 跟踪参数（避免同一骗局因 utm/spm 不同被视为不同）
_TRACK_PARAMS = re.compile(
    r"^(utm_|spm|scid|share_|bd_vid|from|isappinstalled|platform|timestamp|t=|sign|token|nonce|scene)"
    , re.I)


# ---------- 配置（opt-in 开关 / 匿名 id / 端点 / 游标） ----------
def load_conf() -> dict:
    if os.path.exists(CONF):
        try:
            d = json.load(open(CONF, encoding="utf-8"))
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    return {"opt_in": False, "anonymous_id": uuid.uuid4().hex[:16],
            "endpoint": DEFAULT_ENDPOINT, "consent_asked": False}


def save_conf(d: dict):
    try:
        os.makedirs(os.path.dirname(CONF), exist_ok=True)
        json.dump(d, open(CONF, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except Exception:
        pass


def resolve_endpoint(c: dict) -> str:
    """解析实际回传端点：若本地配置存的是旧临时隧道（已下线/每次重启变地址），
    自动回退到当前代码里的 DEFAULT_ENDPOINT，避免老用户被写死的死地址卡死。"""
    ep = (c.get("endpoint") or "").strip()
    if not ep:
        return DEFAULT_ENDPOINT
    return ep


def is_opt_in() -> bool:
    return bool(load_conf().get("opt_in"))


def opt_in(endpoint: str = "") -> dict:
    """程序化开启（供首次鉴定后授权调用）。返回最新配置。"""
    c = load_conf()
    c["opt_in"] = True
    if endpoint:
        c["endpoint"] = endpoint
    if not c.get("endpoint"):
        c["endpoint"] = DEFAULT_ENDPOINT
    c["consent_asked"] = True
    save_conf(c)
    _flush_outbox()  # 用户刚同意，顺手补发本地可能暂存的历史
    return c


def mark_consent_asked():
    """记录「已询问过（用户拒绝）」，避免后续每次鉴定重复唠叨。"""
    c = load_conf()
    c["consent_asked"] = True
    save_conf(c)


def consent_needed() -> bool:
    """是否该弹授权询问：未同意 且 从未问过。"""
    c = load_conf()
    return (not c.get("opt_in")) and (not c.get("consent_asked"))


# ---------- 内容归一化 + 单向 hash（原始内容永不外发） ----------
def normalize_url(u: str) -> str:
    u = (u or "").strip()
    if not u:
        return ""
    u = re.sub(r"^https?://", "", u, flags=re.I)
    u = re.sub(r"^www\.", "", u, flags=re.I)
    # 去掉末尾斜杠
    u = u.rstrip("/")
    # 解析 query，剔除跟踪参数，剩余按 key 排序保证同骗局不同 utm 归一为同一 hash
    if "?" in u:
        base, q = u.split("?", 1)
        parts = []
        for kv in q.split("&"):
            if not kv:
                continue
            k = kv.split("=", 1)[0]
            if _TRACK_PARAMS.match(k):
                continue
            parts.append(kv)
        q = "&".join(sorted(parts))
        u = base + ("?" + q if q else "")
    return u.lower()


def normalize_text(t: str) -> str:
    t = (t or "").strip()
    t = re.sub(r"\s+", " ", t)  # 折叠空白
    return t.lower()


def content_hash(kind: str, content: str) -> str:
    """对原始内容做归一化后 SHA256（单向不可逆）。原始内容不留服务器，只留 hash 用于跨用户去重。
    kind: url / account / text / image_text（图片已提取的文本）。"""
    if not content:
        return ""
    if kind == "url":
        norm = normalize_url(content)
    elif kind == "account":
        norm = content.strip().lower()
    else:  # text / image_text
        norm = normalize_text(content)
    if not norm:
        return ""
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


# ---------- 脱敏贡献构造（不含身份 / 不含原文） ----------
def contribute_verdict(label, risk_type, risk_level, hit_patterns, content_kind,
                       content_hash, note=""):
    """一次判分贡献。label ∈ correct/partial/wrong；只含聚合信号 + 内容 hash，不含原文。"""
    return {
        "type": "verdict",
        "v": SCHEMA_VERSION,
        "skill_id": SKILL_ID,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat() + "Z",
        "label": label,
        "risk_type": risk_type or "",
        "risk_level": risk_level or "",
        "hit_patterns": [str(x) for x in (hit_patterns or []) if x][:20],
        "content_kind": content_kind or "",
        "content_hash": content_hash or "",
        "note": note or "",
    }


def contribute_confirm(risk_type, risk_level, hit_patterns, content_kind, content_hash):
    """用户确认「归入SOP」：只发模式签名（风险类型+分级+命中模式标签+内容 hash），不含具体 URL/账号/原文。"""
    return {
        "type": "confirm",
        "v": SCHEMA_VERSION,
        "skill_id": SKILL_ID,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat() + "Z",
        "risk_type": risk_type or "",
        "risk_level": risk_level or "",
        "hit_patterns": [str(x) for x in (hit_patterns or []) if x][:20],
        "content_kind": content_kind or "",
        "content_hash": content_hash or "",
    }


def contribute_correction(original_level, suggested_level, risk_type, content_kind, content_hash):
    """用户说「不对/改」：发校正信号（原分级→应分级+内容 hash），用于修正误判。"""
    return {
        "type": "correction",
        "v": SCHEMA_VERSION,
        "skill_id": SKILL_ID,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat() + "Z",
        "original_level": original_level or "",
        "suggested_level": suggested_level or "",
        "risk_type": risk_type or "",
        "content_kind": content_kind or "",
        "content_hash": content_hash or "",
    }


def contribute_usage(content_kind, risk_type):
    """匿名使用统计：仅提交类型 + 命中风险类型，用于优先级，不含内容。"""
    return {
        "type": "usage",
        "v": SCHEMA_VERSION,
        "skill_id": SKILL_ID,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat() + "Z",
        "content_kind": content_kind or "",
        "risk_type": risk_type or "",
    }


# ---------- 非阻塞发送（吞异常，绝不阻塞主流程） ----------
def _send(payload: dict, endpoint: str, timeout: int = 5):
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        key = os.environ.get("SCAM_SLAYER_KEY") or SHARED_KEY
        req = urllib.request.Request(
            endpoint, data=data,
            headers={"Content-Type": "application/json",
                     "X-ScamSlayer-Key": key}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except Exception:
        return None


# ---------- 本地待发队列（outbox）：端点不可达时暂存，恢复后自动补发，零丢失 ----------
def _enqueue(payload: dict):
    try:
        rec = dict(payload)
        rec["_queued_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with open(OUTBOX, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _outbox_count() -> int:
    try:
        with open(OUTBOX, encoding="utf-8") as f:
            return sum(1 for l in f if l.strip())
    except Exception:
        return 0


def _flush_outbox(limit: int = 50, timeout: int = 5) -> int:
    if _outbox_count() == 0:
        return 0
    try:
        with open(OUTBOX, encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
    except Exception:
        return 0
    remain, sent = [], 0
    ep = resolve_endpoint(load_conf())
    for ln in lines:
        if sent >= limit:
            remain.append(ln)
            continue
        try:
            payload = json.loads(ln)
        except Exception:
            continue
        payload.pop("_queued_at", None)
        if _send(payload, ep, timeout=timeout):
            sent += 1
        else:
            remain.append(ln)
    try:
        with open(OUTBOX, "w", encoding="utf-8") as f:
            for ln in remain:
                f.write(ln + "\n")
    except Exception:
        pass
    if sent:
        print(f"[feedback] 已自动补发 {sent} 条暂存回传")
    return sent


def _dispatch(payload: dict) -> bool:
    """统一发送：成功即返回；失败进 outbox 不丢。"""
    c = load_conf()
    if not c.get("opt_in"):
        return False
    ep = resolve_endpoint(c)
    st = _send(payload, ep)
    if st:
        return True
    _enqueue(payload)
    return False


# ---------- 对外钩子（供 SKILL.md 确认/判分流程调用） ----------
def maybe_sync_verdict(label, risk_type="", risk_level="", hit_patterns=None,
                        content_kind="", content="", note="", share_content=False):
    """用户判分（对/错/部分对）后调用。仅授权后触发；失败进 outbox 不丢。
    share_content=True（需用户显式同意）时附带原文，供运营研判（隔离库，不进公开看板）。"""
    try:
        _flush_outbox()
        c = load_conf()
        if not c.get("opt_in"):
            return False
        ch = content_hash(content_kind, content)
        p = contribute_verdict(label, risk_type, risk_level, hit_patterns,
                               content_kind, ch, note)
        if share_content and content:
            p["content"] = content
            p["share_content"] = True
        p["aid"] = c.get("anonymous_id") or uuid.uuid4().hex[:16]
        ok = _dispatch(p)
        print(f"[feedback] ✓ 已匿名回传判分（{label}）用于改进鉴别" if ok
              else "[feedback] 端点暂不可达，已暂存本地队列，恢复后自动补发。")
        return True
    except Exception:
        return False


def maybe_sync_confirm(risk_type="", risk_level="", hit_patterns=None,
                       content_kind="", content="", share_content=False):
    """用户确认「归入SOP」时调用。仅发模式签名+内容 hash，不含原文。
    share_content=True（需用户显式同意）时附带原文供运营研判。"""
    try:
        _flush_outbox()
        c = load_conf()
        if not c.get("opt_in"):
            return False
        ch = content_hash(content_kind, content)
        p = contribute_confirm(risk_type, risk_level, hit_patterns, content_kind, ch)
        if share_content and content:
            p["content"] = content
            p["share_content"] = True
        p["aid"] = c.get("anonymous_id") or uuid.uuid4().hex[:16]
        ok = _dispatch(p)
        print(f"[feedback] ✓ 已匿名回传确认模式（{risk_type}）" if ok
              else "[feedback] 端点暂不可达，已暂存本地队列，恢复后自动补发。")
        return True
    except Exception:
        return False


def maybe_sync_correction(original_level, suggested_level, risk_type="",
                          content_kind="", content="", share_content=False):
    """用户说「不对/改」时调用。发校正信号（原分级→应分级+内容 hash）。
    share_content=True（需用户显式同意）时附带原文供运营研判。"""
    try:
        _flush_outbox()
        c = load_conf()
        if not c.get("opt_in"):
            return False
        ch = content_hash(content_kind, content)
        p = contribute_correction(original_level, suggested_level, risk_type, content_kind, ch)
        if share_content and content:
            p["content"] = content
            p["share_content"] = True
        p["aid"] = c.get("anonymous_id") or uuid.uuid4().hex[:16]
        ok = _dispatch(p)
        print(f"[feedback] ✓ 已匿名回传校正信号" if ok
              else "[feedback] 端点暂不可达，已暂存本地队列，恢复后自动补发。")
        return True
    except Exception:
        return False


def maybe_sync_usage(content_kind="", risk_type=""):
    """每次鉴定后（无论是否确认）调用，匿名统计提交类型分布，用于优先级。"""
    try:
        c = load_conf()
        if not c.get("opt_in"):
            return False
        p = contribute_usage(content_kind, risk_type)
        p["aid"] = c.get("anonymous_id") or uuid.uuid4().hex[:16]
        _dispatch(p)
        return True
    except Exception:
        return False


# ---------- 逐次显式同意：回传本次鉴定原文给开发者研判 ----------
def maybe_share_content(content="", content_kind="text", risk_type="", risk_level="",
                        label="", hit_patterns=None, note=""):
    """用户**逐次显式同意**把本次鉴别的【原文】回传给开发者，用于研判鉴别对象是否确为有害信息。

    与全局 opt-in 解耦：只要用户当次明确同意即可回传，**不依赖**匿名反馈总开关；
    但仍是逐条独立征询、默认否（绝不静默全采，PIPL 明示同意）。明文只落开发者侧
    隔离库（收集端 operator_content.jsonl），绝不进公开零收集看板 contrib.jsonl。

    参数：
      content      本次用户提交的原文（链接/文本/账号），为空则不回传
      content_kind url / account / text / image_text
      risk_type / risk_level / label / hit_patterns / note  来自本次五段式结论的元数据
    """
    try:
        if not content or not content.strip():
            return False
        c = load_conf()
        ch = content_hash(content_kind, content)
        p = {
            "type": "verdict",
            "v": SCHEMA_VERSION,
            "skill_id": SKILL_ID,
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat() + "Z",
            "label": label or "",
            "risk_type": risk_type or "",
            "risk_level": risk_level or "",
            "hit_patterns": [str(x) for x in (hit_patterns or []) if x][:20],
            "content_kind": content_kind or "",
            "content_hash": ch,
            "content": content,
            "share_content": True,
            "note": note or "",
        }
        p["aid"] = c.get("anonymous_id") or uuid.uuid4().hex[:16]
        st = _send(p, resolve_endpoint(c))
        if st:
            print("[feedback] ✓ 已按你的授权回传本次鉴别原文给开发者用于研判（隔离库，不进公开看板）。")
            return True
        _enqueue(p)
        print("[feedback] 端点暂不可达，已将本次原文暂存本地队列，恢复后自动补发。")
        return False
    except Exception:
        return False


# ---------- 交互终端一次性授权询问 ----------
def maybe_prompt_consent():
    """仅交互终端（不是 agent/自动化）且从未问过时，弹一次授权询问。
    返回 True 表示已同意（本次或历史），False 表示未同意/未问。"""
    try:
        if is_opt_in():
            return True
        if not consent_needed():
            return False
        if not sys.stdin or not sys.stdin.isatty():
            return False  # 非交互（agent/管道）→ 不弹，保持默认零收集
        print("\n── Scam-Slayer 匿名反馈（可选，默认关闭）──")
        print("  开启后，你每次「鉴定确认 / 判分 / 校正」会回传**脱敏**信号（结论判分、")
        print("  确认的模式标签、内容 SHA256 单向哈希），帮助所有用户把骗局识别得更准。")
        print("  ⚠️ 绝不收集：原始链接/账号/截图原文、你的身份、聊天内容。可随时 --opt-out 关闭。")
        ans = input("  是否开启匿名反馈？(y/回车=开启，n=暂不)：").strip().lower()
        if ans in ("y", "yes", ""):
            opt_in()
            print("  ✓ 已开启。随时 `python feedback_sync.py --opt-out` 关闭。")
            return True
        else:
            mark_consent_asked()
            print("  ✗ 本次不开启（默认零收集）。以后随时 `python feedback_sync.py --opt-in` 开启。")
            return False
    except Exception:
        return False


# ---------- CLI ----------
def main():
    import argparse
    ap = argparse.ArgumentParser(description="Scam-Slayer 可选跨用户反馈同步（默认关闭，需显式开启）")
    ap.add_argument("--opt-in", action="store_true", help="开启匿名反馈同步")
    ap.add_argument("--opt-out", action="store_true", help="关闭反馈同步")
    ap.add_argument("--endpoint", default="", help="自定义收集端点 URL（随 --opt-in 设置）")
    ap.add_argument("--status", action="store_true", help="查看当前开关/匿名id/端点/游标")
    ap.add_argument("--flush", action="store_true", help="立即回传待发送的贡献")
    ap.add_argument("--share-json", action="store_true",
                    help="从 stdin 读 JSON {content,content_kind,risk_type,risk_level,label,hit_patterns,note}，"
                         "按用户逐次显式同意回传本次鉴别原文给开发者研判（独立于 opt-in，需用户当次明确同意）")
    args = ap.parse_args()

    if args.share_json:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        try:
            d = json.loads(raw) if raw.strip() else {}
        except Exception:
            print("[feedback] 无法解析 stdin JSON"); return
        if not isinstance(d, dict):
            print("[feedback] stdin 须为 JSON 对象"); return
        ok = maybe_share_content(
            content=d.get("content", ""),
            content_kind=d.get("content_kind", "text"),
            risk_type=d.get("risk_type", ""),
            risk_level=d.get("risk_level", ""),
            label=d.get("label", ""),
            hit_patterns=d.get("hit_patterns") or None,
            note=d.get("note", ""),
        )
        print("[feedback] 原文回传完成（隔离库）。" if ok else "[feedback] 原文回传未成功（已暂存或内容为空）。")
        return

    if args.status:
        c = load_conf()
        print("── Scam-Slayer 反馈同步状态 ──")
        print(f"  收集开关 : {'已开启' if c.get('opt_in') else '关闭（默认，零收集）'}")
        print(f"  匿名 id  : {c.get('anonymous_id')}")
        print(f"  端点     : {c.get('endpoint')}")
        print("  收集内容 : 判分/确认模式签名/校正/匿名统计 + 内容 SHA256(单向)")
        print("  绝不收集 : 原始链接/账号/截图原文/身份/聊天内容")
        print(f"  本地待发队列 : {_outbox_count()} 条（端点不可达时暂存，恢复后自动补发，零丢失）")
        return

    if args.opt_out:
        c = load_conf()
        c["opt_in"] = False
        save_conf(c)
        print("✓ 反馈同步已关闭。Scam-Slayer 不会再向外发送任何数据（默认即此状态）。")
        return

    if args.flush:
        if not is_opt_in():
            print("[feedback] 未开启同步，先 --opt-in。")
        else:
            sent = _flush_outbox()
            print(f"[feedback] flush 完成：本次补发 {sent} 条历史暂存；队列剩余见 {OUTBOX}")
        return

    if args.opt_in:
        c = load_conf()
        c["opt_in"] = True
        if args.endpoint:
            c["endpoint"] = args.endpoint
        save_conf(c)
        print("✓ 已开启【匿名】反馈同步。每次鉴定确认/判分/校正后会回传以下内容（绝不收原文）：")
        print("   · 你对鉴别结论的判分（对/部分对/错）")
        print("   · 确认的模式签名（风险类型 + 分级 + 命中红flag标签）")
        print("   · 校正信号（原分级 → 应分级）")
        print("   · 内容的 SHA256 单向哈希（用于跨用户识别同一骗局，原始内容不留服务器）")
        print(f"   匿名 id：{c.get('anonymous_id')}（仅去重，不关联身份）")
        print(f"   收集端点：{c.get('endpoint')}")
        print("   随时 --opt-out 关闭；作为数据贡献者，你有权要求删除自己的贡献（联系开发者）。")
        return

    c = load_conf()
    if not c.get("opt_in"):
        print("[feedback] 反馈同步默认关闭（零收集）。开启：python feedback_sync.py --opt-in")
        print("       查看状态：python feedback_sync.py --status")
    else:
        print("[feedback] 已开启。--opt-out 可关闭；--status 看详情；--flush 立即回传。")


if __name__ == "__main__":
    main()
