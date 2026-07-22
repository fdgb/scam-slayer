#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_kb.py — scam-slayer 知识库热更新消费者（本地 skill 侧）。

作用：让已安装的本地 skill 在不重装的前提下，自动拿到 GitHub 上最新的知识库。

每次 skill 会话开始时（或每日自动化触发）运行：
  1. 拉取 GitHub 上的 version.json；
  2. 与本地下载缓存 cache/version.json 逐个文件比对 sha256；
  3. 有变更 / 缺失的文件，把最新 data/*.md 下载进 cache/；
  4. 把远程 version.json 存为 cache/version.json，作为下次比对基线。

SKILL.md 运行时优先读取 cache/ 下的知识文件；cache/ 缺失或为空时回退 references/。

用法:
    python3 update_kb.py            # 检查并应用更新
    python3 update_kb.py --check    # 仅检查是否有更新，不下载
    python3 update_kb.py --force    # 强制重新下载全部文件

退出码:  始终为 0。网络失败 / 校验失败只告警，绝不阻断 skill 正常运行。
"""
import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

# ---- 配置（公开仓库，免 token 可读）----
REPO = "fdgb/scam-slayer"
BRANCH = "main"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SKILL_DIR, "cache")
CACHE_VERSION = os.path.join(CACHE_DIR, "version.json")

HTTP_TIMEOUT = 15


def log(msg: str) -> None:
    print(f"[update_kb] {msg}", flush=True)


def http_get_bytes(url: str) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": "scam-slayer-updater/1.0"}
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return resp.read()


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_local_manifest() -> dict | None:
    if os.path.exists(CACHE_VERSION):
        try:
            with open(CACHE_VERSION, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="scam-slayer KB hot-updater")
    ap.add_argument("--check", action="store_true", help="仅检查，不下载")
    ap.add_argument("--force", action="store_true", help="强制全量重新下载")
    args = ap.parse_args()

    local = load_local_manifest()
    if local is None:
        log("本地无缓存，将进行首次全量同步")
    else:
        log(f"本地缓存版本: {local.get('version', 'unknown')}")

    try:
        remote_raw = http_get_bytes(f"{RAW_BASE}/version.json")
        remote = json.loads(remote_raw.decode("utf-8"))
    except Exception as e:
        log(f"⚠️ 无法连接 GitHub（{e}），使用本地现有知识库，本次跳过更新")
        return 0

    remote_files = remote.get("files", {})
    local_files = (local or {}).get("files", {})
    log(f"远程版本: {remote.get('version')} | 文件数: {len(remote_files)}")

    plan = []
    for name, meta in remote_files.items():
        rel = f"data/{name}"
        dest = os.path.join(CACHE_DIR, name)
        remote_sha = meta.get("sha256")
        local_sha = local_files.get(name, {}).get("sha256")
        if args.force or remote_sha != local_sha or not os.path.exists(dest):
            plan.append((name, rel, dest, remote_sha))

    if not plan:
        log("✅ 知识库已是最新，无需更新")
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(CACHE_VERSION, "w", encoding="utf-8") as f:
            json.dump(remote, f, ensure_ascii=False, indent=2)
        return 0

    if args.check:
        log(f"🔔 发现 {len(plan)} 个文件可更新，运行不带 --check 以应用")
        return 0

    ok = 0
    for name, rel, dest, remote_sha in plan:
        try:
            data = http_get_bytes(f"{RAW_BASE}/{rel}")
            actual = sha256_of(data)
            if remote_sha and actual != remote_sha:
                log(f"⚠️ {name} 校验失败（sha256 不符），已丢弃")
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            tmp = dest + ".tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, dest)
            ok += 1
            log(f"⬇️ 已更新 {name} ({len(data)} bytes)")
        except Exception as e:
            log(f"⚠️ 下载 {name} 失败: {e}")

    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(CACHE_VERSION, "w", encoding="utf-8") as f:
        json.dump(remote, f, ensure_ascii=False, indent=2)
    log(f"✅ 同步完成: 本批更新 {ok}/{len(plan)} 个文件 | 缓存版本 {remote.get('version')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
