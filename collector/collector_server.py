#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scam-Slayer × MusicMood · 多租户收集端点 + 聚合（开发者侧基建，随包不发布到分发包）
================================================================================
最小可运行收集端点，纯 Python 标准库，本地即可起：
    python collector_server.py                       # 起 HTTP 服务，按 skill_id 分目录落盘
    python collector_server.py --aggregate --skill scam-slayer
    python collector_server.py --purge   --skill scam-slayer
    python collector_server.py --host 0.0.0.0 --port 8080

接收协议（与客户端一致）：
    POST /v1/contribute    body=JSON（含 skill_id 字段）
    → 按 skill_id 分目录追加到 /data/<skill_id>/contrib.jsonl（含服务端接收时间 + 匿名 aid）
    → 若记录含 share_content=True 且 content（用户逐次显式同意的原文），明文另落
      /data/<skill_id>/operator_content.jsonl（运营研判隔离库，与公开 contrib 严格分开；
      contrib 落盘前已剔除 content/share_content，绝不污染零收集看板）

多租户隔离（用户核心诉求：musicmood 与 scam-slayer 数据绝不混）：
  - 落盘：/data/<skill_id>/contrib.jsonl 完全分开
  - 密钥：每个租户独立 env（MUSICMOOD_KEY / SCAM_SLAYER_KEY），错租户/无 key → 401
  - 聚合：--aggregate --skill <id> 只聚合该租户；musicmood→global_weights.json，scam-slayer→cross_user_patterns.json
  - 删除：--purge --skill <id> 只删该租户（PIPL 删除权）

向后兼容：无 skill_id 的旧 musicmood 客户端 → 默认 skill_id=musicmood，老数据不丢。

防污染：入站结构校验(_valid 按租户类型) + 单条 64KB 上限 + 单 aid 频率限制。
⚠️ 合规提示：这是处理他人个人数据的服务端，上线前请确认 PIPL 合规（告知/删除权/存储安全）。
"""
import os, sys, json, argparse, datetime, time
from http.server import BaseHTTPRequestHandler, HTTPServer

# 数据根目录：每个租户一个子目录
DATA_ROOT = os.environ.get("COLLECTOR_DATA_ROOT", "/data")
DEFAULT_SKILL = "musicmood"  # 向后兼容：无 skill_id 的旧客户端视为 musicmood

# 每个租户的密钥（部署端设环境变量即开启校验；不设为关闭校验，本地自测/兼容）
SKILL_KEYS = {
    "musicmood": os.environ.get("MUSICMOOD_KEY", ""),
    "scam-slayer": os.environ.get("SCAM_SLAYER_KEY", ""),
}
# 每个租户对应的请求头名
SKILL_HEADER = {
    "musicmood": "X-MusicMood-Key",
    "scam-slayer": "X-ScamSlayer-Key",
}

# —— 防污染：入站护栏 ——
MAX_BYTES = 64 * 1024
RATE_LIMIT = 120            # 同一匿名 id 在 RATE_WINDOW 秒内最多 120 条，防刷量
RATE_WINDOW = 60
_rate = {}


def store_path(skill_id: str) -> str:
    d = os.path.join(DATA_ROOT, skill_id)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "contrib.jsonl")


def operator_path(skill_id: str) -> str:
    """运营研判隔离库：仅当 share_content=True 且含 content 时，明文才落此文件。
    与公开零收集看板 contrib.jsonl 严格分开，绝不混写。"""
    d = os.path.join(DATA_ROOT, skill_id)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "operator_content.jsonl")


def out_path(skill_id: str) -> str:
    """聚合产物路径（按租户类型区分文件名）。"""
    d = os.path.join(DATA_ROOT, skill_id)
    os.makedirs(d, exist_ok=True)
    if skill_id == "musicmood":
        return os.path.join(d, "global_weights.json")
    return os.path.join(d, "cross_user_patterns.json")


# ---------------- HTTP 接收 ----------------
class Handler(BaseHTTPRequestHandler):
    def _recv(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n) if n else b"{}"
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def _skill_of(self, p):
        return (p.get("skill_id") or DEFAULT_SKILL) if isinstance(p, dict) else DEFAULT_SKILL

    # ---------------- 防污染：入站结构校验（按租户类型） ----------------
    def _valid(self, skill_id, p):
        if not isinstance(p, dict):
            return False
        t = p.get("type")
        if skill_id == "musicmood":
            if t == "session":
                if not isinstance(p.get("mood"), str) or not p["mood"].strip():
                    return False
                it = p.get("intensity")
                if it is not None:
                    try:
                        if not (0 <= int(it) <= 100):
                            return False
                    except Exception:
                        return False
                eg = p.get("extended_genres")
                if eg is not None and (not isinstance(eg, list) or not all(isinstance(x, str) for x in eg)):
                    return False
                es = p.get("extended_song_ids")
                if es is not None and (not isinstance(es, list) or not all(isinstance(x, int) for x in es)):
                    return False
                return True
            if t == "feedback":
                return p.get("rating") in ("on", "off") and isinstance(p.get("song"), str) and bool(p["song"].strip())
            if t == "feedback_batch":
                items = p.get("items")
                if not isinstance(items, list) or not items:
                    return False
                return all(isinstance(i, dict) and i.get("rating") in ("on", "off")
                           and isinstance(i.get("song"), str) and i["song"].strip() for i in items)
            return False
        # scam-slayer 租户
        if t == "verdict":
            return p.get("label") in ("correct", "partial", "wrong") \
                and isinstance(p.get("content_hash"), str)
        if t == "confirm":
            return isinstance(p.get("risk_type"), str) and isinstance(p.get("content_hash"), str)
        if t == "correction":
            return isinstance(p.get("original_level"), str) and isinstance(p.get("suggested_level"), str) \
                and isinstance(p.get("content_hash"), str)
        if t == "usage":
            return isinstance(p.get("content_kind"), str)
        return False

    def _rate_ok(self, aid):
        if not aid:
            return True
        now = time.time()
        global _rate
        _rate = {k: [t for t in v if now - t < RATE_WINDOW] for k, v in _rate.items()}
        hits = _rate.get(aid, [])
        if len(hits) >= RATE_LIMIT:
            return False
        hits.append(now)
        _rate[aid] = hits
        return True

    def do_POST(self):
        if self.path.rstrip("/") not in ("/v1/contribute",):
            self.send_response(404); self.end_headers(); return
        payload = self._recv()
        if not isinstance(payload, dict):
            self.send_response(400); self.end_headers(); return
        skill_id = self._skill_of(payload)
        if skill_id not in SKILL_KEYS:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":false,"error":"unknown_skill_id"}')
            return
        # 每租户独立密钥校验
        expected = SKILL_KEYS.get(skill_id, "")
        if expected:
            hdr = SKILL_HEADER.get(skill_id, "X-Key")
            if self.headers.get(hdr) != expected:
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":false,"error":"unauthorized"}')
                return
        # 防超大 payload
        try:
            n = int(self.headers.get("Content-Length", 0))
        except Exception:
            n = 0
        if n > MAX_BYTES:
            self.send_response(413); self.end_headers()
            self.wfile.write(b'{"ok":false,"error":"payload_too_large"}'); return
        # 防污染：入站结构校验
        if not self._valid(skill_id, payload):
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":false,"error":"invalid_payload"}')
            return
        # 防刷量
        if not self._rate_ok(payload.get("aid") or ""):
            self.send_response(429); self.end_headers()
            self.wfile.write(b'{"ok":false,"error":"rate_limited"}'); return
        try:
            rec = dict(payload)
            rec["_skill_id"] = skill_id
            rec["_received_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            # 明文隔离：含 share_content + content 的记录，原文只落运营研判隔离库，
            # 公开 contrib.jsonl 必须去掉 content / share_content（绝不污染零收集看板）。
            op_rec = None
            if rec.get("share_content") and rec.get("content"):
                op_rec = dict(rec)          # 保留明文，落 operator_content.jsonl
                rec.pop("content", None)    # 公开库剔除原文
                rec.pop("share_content", None)
            with open(store_path(skill_id), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if op_rec:
                with open(operator_path(skill_id), "a", encoding="utf-8") as f:
                    f.write(json.dumps(op_rec, ensure_ascii=False) + "\n")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        except Exception as ex:
            self.send_response(500); self.end_headers(); self.wfile.write(str(ex).encode())

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({
            "ok": true, "service": "multitenant-collector",
            "tenants": list(SKILL_KEYS.keys()), "default_skill": DEFAULT_SKILL
        }).encode())

    def log_message(self, *a):
        pass


# ---------------- 聚合（按租户） ----------------
def aggregate(skill_id: str):
    in_path = store_path(skill_id)
    out_path_file = out_path(skill_id)
    if not os.path.exists(in_path):
        print(f"[aggregate] 无贡献文件 {in_path}（租户 {skill_id}），跳过。")
        return None
    rows = []
    with open(in_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue

    if skill_id == "musicmood":
        return _aggregate_musicmood(rows, out_path_file)
    return _aggregate_scam_slayer(rows, out_path_file)


def _aggregate_musicmood(rows, out_path_file):
    sessions, feedbacks = [], []
    for r in rows:
        if r.get("type") == "session":
            sessions.append(r)
        elif r.get("type") in ("feedback", "feedback_batch"):
            if r.get("type") == "feedback_batch":
                feedbacks.extend(r.get("items", []))
            else:
                feedbacks.append(r)
    count_mood, total_mood, count_all, total_all = {}, {}, {}, 0
    for s in sessions:
        m = s.get("mood")
        if not m:
            continue
        genres = [g for g in (s.get("extended_genres") or []) if g]
        count_mood.setdefault(m, {})
        total_mood[m] = total_mood.get(m, 0) + len(genres)
        for g in genres:
            count_mood[m][g] = count_mood[m].get(g, 0) + 1
            count_all[g] = count_all.get(g, 0) + 1
            total_all += 1
    mood_genre_prior = {}
    if total_all > 0:
        for m, gmap in count_mood.items():
            if not total_mood.get(m):
                continue
            mood_genre_prior[m] = {}
            for g, n in gmap.items():
                rate_given = n / total_mood[m]
                base = count_all[g] / total_all
                if base <= 0:
                    continue
                mood_genre_prior[m][g] = round(max(0.5, min(2.0, rate_given / base)), 3)
    on_c, off_c = {}, {}
    for fb in feedbacks:
        s = str(fb.get("song") or "").lower().strip()
        r = fb.get("rating")
        if not s or r not in ("on", "off"):
            continue
        on_c[s] = on_c.get(s, 0) + 1 if r == "on" else on_c.get(s, 0)
        off_c[s] = off_c.get(s, 0) + 1 if r == "off" else off_c.get(s, 0)
    song_keep_rate, song_skip_rate = {}, {}
    for s in set(list(on_c) + list(off_c)):
        tot = on_c.get(s, 0) + off_c.get(s, 0)
        if tot < 2:
            continue
        song_keep_rate[s] = round(on_c.get(s, 0) / tot, 3)
        song_skip_rate[s] = round(off_c.get(s, 0) / tot, 3)
    gw = {"version": 1, "updated": datetime.date.today().isoformat(),
          "skill_id": "musicmood",
          "note": "匿名聚合权重；下发到技能根目录 global_weights.json 后自动回灌（缺失则 no-op）。",
          "mood_genre_prior": mood_genre_prior, "song_keep_rate": song_keep_rate,
          "song_skip_rate": song_skip_rate}
    with open(out_path_file, "w", encoding="utf-8") as f:
        json.dump(gw, f, ensure_ascii=False, indent=2)
    print(f"[aggregate] musicmood 会话 {len(sessions)} / 反馈 {len(feedbacks)} → {out_path_file}")
    return gw


def _aggregate_scam_slayer(rows, out_path_file):
    """scam-slayer 跨用户聚合：同骗局命中人数、风险类型分布、模式高频榜、判分准确率估计。"""
    hash_users = {}        # content_hash -> set(aid)  同骗局被多少不同用户遇到
    hash_risk = {}         # content_hash -> risk_type
    risk_type_count = {}   # 风险类型 -> 次数
    pattern_freq = {}      # hit_pattern -> 次数（确认/判分中命中的模式）
    verdict_acc = {}       # risk_type -> {correct, partial, wrong}
    corrections = 0
    total = len(rows)
    for r in rows:
        aid = r.get("aid", "")
        t = r.get("type")
        ch = r.get("content_hash") or ""
        rt = r.get("risk_type") or ""
        if rt:
            risk_type_count[rt] = risk_type_count.get(rt, 0) + 1
        if ch:
            hash_users.setdefault(ch, set()).add(aid)
            if rt:
                hash_risk[ch] = rt
        for p in (r.get("hit_patterns") or []):
            pattern_freq[p] = pattern_freq.get(p, 0) + 1
        if t == "verdict":
            va = verdict_acc.setdefault(rt or "_all", {"correct": 0, "partial": 0, "wrong": 0})
            lab = r.get("label")
            if lab in va:
                va[lab] += 1
        elif t == "correction":
            corrections += 1

    # 同骗局热度榜（被 ≥2 个不同用户遇到）
    hotspot = []
    for ch, users in hash_users.items():
        if len(users) >= 2:
            hotspot.append({"content_hash": ch, "users": len(users),
                            "risk_type": hash_risk.get(ch, "")})
    hotspot.sort(key=lambda x: -x["users"])

    # 判分准确率估计（仅作内部参考，不对外宣称模型）
    accuracy = {}
    for rt, c in verdict_acc.items():
        tot = c["correct"] + c["partial"] + c["wrong"]
        if tot >= 3:  # 样本太少不采纳
            accuracy[rt] = {
                "samples": tot,
                "correct_rate": round(c["correct"] / tot, 3),
                "wrong_rate": round(c["wrong"] / tot, 3),
            }

    cup = {
        "version": 1, "updated": datetime.date.today().isoformat(),
        "skill_id": "scam-slayer",
        "note": "匿名跨用户聚合（content_hash 单向，不可反推原文）。仅供开发者优先级参考，非模型权重。",
        "total_records": total,
        "distinct_scam_hashes": len(hash_users),
        "hotspot_same_scam_by_users": hotspot[:100],   # 同骗局热度榜 Top100
        "risk_type_distribution": risk_type_count,
        "top_patterns": dict(sorted(pattern_freq.items(), key=lambda x: -x[1])[:50]),
        "verdict_accuracy_estimate": accuracy,
        "corrections_received": corrections,
    }
    with open(out_path_file, "w", encoding="utf-8") as f:
        json.dump(cup, f, ensure_ascii=False, indent=2)
    print(f"[aggregate] scam-slayer 记录 {total} / 不同骗局 hash {len(hash_users)} / 校正 {corrections} → {out_path_file}")
    print(f"  同骗局≥2人: {len(hotspot)} 个；风险类型 {len(risk_type_count)} 类；高频模式 {len(pattern_freq)} 个")
    return cup


def purge(skill_id: str):
    p = store_path(skill_id)
    if os.path.exists(p):
        os.remove(p)
        print(f"[purge] 已删除租户 {skill_id} 的落盘数据：{p}")
    else:
        print(f"[purge] 租户 {skill_id} 无数据，无需删除。")


def main():
    global DATA_ROOT
    ap = argparse.ArgumentParser(description="多租户收集端点 + 聚合（musicmood / scam-slayer 隔离）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--data-root", default="")
    ap.add_argument("--aggregate", action="store_true")
    ap.add_argument("--purge", action="store_true")
    ap.add_argument("--skill", default=DEFAULT_SKILL, help="聚合/删除针对的租户")
    args = ap.parse_args()
    if args.data_root:
        DATA_ROOT = args.data_root
    os.makedirs(DATA_ROOT, exist_ok=True)
    if args.purge:
        purge(args.skill)
        return
    if args.aggregate:
        aggregate(args.skill)
        return
    print(f"[server] 监听 {args.host}:{args.port}  POST /v1/contribute  (租户: {list(SKILL_KEYS.keys())})")
    print(f"        数据根: {DATA_ROOT}  默认租户: {DEFAULT_SKILL}")
    HTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
