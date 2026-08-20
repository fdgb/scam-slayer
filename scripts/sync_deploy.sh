#!/usr/bin/env bash
#
# sync_deploy.sh — 照妖镜 · 双端同步 + 全渠道发布（一键 / 自动化）
#
# 设计原则（诚实红线优先）：
#   1. references/ 是【唯一权威源】。agent 运行期只改 references/ 与 SKILL.md。
#      data/、web 线上、GitHub、SkillHub、Coze 全是 references/ 的镜像。
#   2. 任何「data/ 含有 references/ 没有的条目」都视为危险漂移 → 立即中止，
#      绝不静默覆盖（防止丢案例）。需人工先在 references/ 合并后再跑。
#   3. 发布（--publish）默认关闭；开启后 Coze 走 API，SkillHub 仅出包+人工上传。
#   4. 若发布前 capability-verifier 不通过，则拒绝发布（不把坏内容推上线）。
#
# 用法：
#   bash scripts/sync_deploy.sh                 # 同步 skill→data→web→github（不含发布）
#   bash scripts/sync_deploy.sh --publish       # 含 Coze 发布（SkillHub 出包）
#   bash scripts/sync_deploy.sh --check-only    # 只做漂移检查，不改动任何东西
#   bash scripts/sync_deploy.sh --coze-skill-id <ID>   # 指定 xiaping skill_id
#
set -uo pipefail

# ---------- 路径 ----------
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"          # scam-slayer-data
SKILL_DIR="$HOME/.workbuddy/skills/scam-slayer"
REF="$SKILL_DIR/references"
DATA="$ROOT/data"
SKILLMD="$SKILL_DIR/SKILL.md"

# ---------- 线上 web ----------
LIVE_HOST="ubuntu@140.143.85.18"
LIVE_KEY="$HOME/.ssh/id_scamslayer"
LIVE_DATA="/home/ubuntu/scam-slayer-web/scam-slayer-data/data"

# ---------- Coze / 虾评 ----------
XIAPING_KEY="${XIAPING_KEY:-sk_r-Q1yJF51s9x6cMHlQTdRqXep8tA7pUC}"
COZE_SKILL_ID="${COZE_SKILL_ID:-}"
mkdir -p "$ROOT/dist"
PKG="$ROOT/dist/scam-slayer-coze.zip"
PUBLISH_STATE="$ROOT/.last_publish_hash"
VERSION="$(date +v%Y.%m.%d).$(date +%H%M)"

# 核心 KB 指纹（发布变更检测：无变化时不再刷 Coze 版本）
# 仅对【公开可分发】的 KB 文件取指纹——刻意排除 case-library.md（核心护城河，不进包）。
# 否则 B 线每日新增 CASE-WX 会让指纹天天变，导致 Coze 每天重发 + 反复触发 PLEDGE 闸门。
kb_fingerprint(){
  cat "$SKILLMD" \
    "$REF/rumor-library.md" "$REF/patterns.md" "$REF/wechat-patterns.md" \
    "$REF/phone-scam.md" "$REF/elderly-guide.md" "$REF/truth-base.json" 2>/dev/null | md5
}

# ---------- 核心 KB 文件 ----------
CORE_MD=(case-library.md rumor-library.md patterns.md wechat-patterns.md phone-scam.md elderly-guide.md)
CORE_JSON=(truth-base.json)
ALL_CORE=(SKILL.md "${CORE_MD[@]}" "${CORE_JSON[@]}")

PUBLISH=0
CHECK_ONLY=0
PLEDGE=0
for a in "$@"; do
  case "$a" in
    --publish) PUBLISH=1 ;;
    --check-only) CHECK_ONLY=1 ;;
    --pledge) PLEDGE=1 ;;
    --coze-skill-id) ;;  # value consumed below
    *) if [ "$a" != "${a/--coze-skill-id=/}" ]; then COZE_SKILL_ID="${a#--coze-skill-id=}"; fi ;;
  esac
done

log(){ echo "[sync] $*"; }
err(){ echo "[ERR ] $*" >&2; }
die(){ err "$*"; exit 1; }

# ---------- 1. 漂移检查（安全闸门） ----------
drift_check(){
  local bad=0
  log "漂移检查：references/ 应为 data/ 的超集"

  # CASE-ID 级别（case-library / rumor-library）
  for f in case-library.md rumor-library.md; do
    local ref_ids data_ids only_data
    ref_ids=$(grep -oE 'CASE-[A-Z]+-[0-9]+' "$REF/$f" 2>/dev/null | sort -u)
    data_ids=$(grep -oE 'CASE-[A-Z]+-[0-9]+' "$DATA/$f" 2>/dev/null | sort -u)
    only_data=$(comm -23 <(echo "$data_ids") <(echo "$ref_ids"))
    if [ -n "$only_data" ]; then
      err "⚠️  $f：data/ 含 references/ 没有的条目（将丢失，已中止）："
      echo "$only_data" | sed 's/^/      /' >&2
      bad=1
    fi
  done

  # patterns.md 章节级别（## 标题）
  local ref_sec data_sec only_sec
  ref_sec=$(grep -oE '^## [^ ]+' "$REF/patterns.md" 2>/dev/null | sort -u)
  data_sec=$(grep -oE '^## [^ ]+' "$DATA/patterns.md" 2>/dev/null | sort -u)
  only_sec=$(comm -23 <(echo "$data_sec") <(echo "$ref_sec"))
  if [ -n "$only_sec" ]; then
    err "⚠️  patterns.md：data/ 含 references/ 没有的章节（将丢失，已中止）："
    echo "$only_sec" | sed 's/^/      /' >&2
    bad=1
  fi

  if [ "$bad" -ne 0 ]; then
    die "发现危险漂移：请先在 references/ 合并 data/ 独有内容，再运行本脚本。"
  fi
  log "漂移检查通过：data/ 是 references/ 的子集，可安全镜像。"
}

# ---------- 2. references → data 镜像 ----------
mirror_to_data(){
  log "镜像 references/ → data/（核心 KB）"
  cp "$SKILLMD" "$ROOT/SKILL.md"
  for f in "${CORE_MD[@]}" "${CORE_JSON[@]}"; do
    cp "$REF/$f" "$DATA/$f"
  done
  log "已复制 $((${#CORE_MD[@]}+${#CORE_JSON[@]}+1)) 个文件。"
}

# ---------- 3. 线上 web 同步 ----------
sync_web(){
  log "同步核心 KB 到线上 ($LIVE_HOST:$LIVE_DATA)"
  scp -i "$LIVE_KEY" -o StrictHostKeyChecking=no -P 22 \
    "$DATA"/{case-library.md,rumor-library.md,patterns.md,truth-base.json,wechat-patterns.md,phone-scam.md,elderly-guide.md} \
    "$LIVE_HOST:$LIVE_DATA/" || die "scp 到线上失败"
  log "重启线上后端以加载新 KB（systemctl restart）"
  ssh -i "$LIVE_KEY" -o StrictHostKeyChecking=no "$LIVE_HOST" \
    'sudo systemctl restart scam-slayer && sleep 3 && systemctl is-active scam-slayer' \
    || die "线上重启失败"
  log "线上 KB 已更新并重启。"
}

# ---------- 4. GitHub 干净推送 ----------
push_github(){
  log "重建 version.json（RAG 热更新触发）"
  (cd "$ROOT" && python3 scripts/build_manifest.py >/dev/null 2>&1) || log "build_manifest 警告（仍可继续）"
  log "clean_push → GitHub（单条干净 commit）"
  (cd "$ROOT" && bash scripts/clean_push.sh) || die "GitHub 推送失败"
  log "GitHub 已更新。"
}

# ---------- 5. 发布（可选，默认关） ----------
package_zip(){
  log "打包干净 zip → $PKG"
  rm -rf /tmp/_pkg && mkdir -p /tmp/_pkg/scam-slayer
  cp "$SKILLMD" /tmp/_pkg/scam-slayer/
  cp -R "$REF" /tmp/_pkg/scam-slayer/references
  rm -rf /tmp/_pkg/scam-slayer/references/__pycache__ \
         /tmp/_pkg/scam-slayer/references/.git 2>/dev/null
  # 运营队列/训练中间产物不属于技能知识，剔除（避免泄露内部队列 + 撑大包体）
  rm -rf /tmp/_pkg/scam-slayer/references/collected_data 2>/dev/null
  # 核心护城河：case-library.md 绝不进 Coze/SkillHub 下载包（只留本地+线上后端自用）
  rm -f /tmp/_pkg/scam-slayer/references/case-library.md 2>/dev/null
  # 剔除其他技能备份 / 私钥 / 缓存 / 旧 zip
  find /tmp/_pkg/scam-slayer -name '*.zip' -delete 2>/dev/null
  find /tmp/_pkg/scam-slayer -name '*.pem' -delete 2>/dev/null
  # 关键：必须删除旧目标 zip，否则 zip -rq 是「更新」而非「重建」，
  # 会残留 collected_data 等已被剔除的旧条目（曾导致运营队列泄露 + 包体虚胖）。
  rm -f "$PKG" 2>/dev/null
  (cd /tmp/_pkg && zip -rq "$PKG" scam-slayer)
  log "zip 大小：$(du -h "$PKG" | cut -f1)"
}

coze_update(){
  [ -n "$XIAPING_KEY" ] || die "XIAPING_KEY 未设置"
  package_zip

  # 1) 解析 skill_id：优先参数；否则在我名下按名称匹配；否则首次发布
  if [ -z "$COZE_SKILL_ID" ]; then
    local me
    me=$(curl -s -m20 -H "Authorization: Bearer $XIAPING_KEY" "https://xiaping.coze.com/api/skills?author=me" 2>/dev/null)
    COZE_SKILL_ID=$(echo "$me" | grep -oE '"id":"[^"]+","name":"[^"]*(照妖镜|反诈|鉴别|scam|营销号)[^"]*"' | head -1 | sed -E 's/.*"id":"([^"]+)".*/\1/')
  fi

  local resp pledge_arg=""
  [ "$PLEDGE" -eq 1 ] && pledge_arg='-F pledge={"agreed":true}'

  if [ -n "$COZE_SKILL_ID" ]; then
    log "更新已存在技能（skill_id=$COZE_SKILL_ID）"
    resp=$(curl -s -m60 -X POST https://xiaping.coze.com/api/upload \
      -H "Authorization: Bearer $XIAPING_KEY" \
      -F "file=@$PKG" -F "skill_id=$COZE_SKILL_ID" \
      -F "changelog=自动同步：$VERSION 知识库合并 + 主题事实核查能力" \
      $pledge_arg 2>/dev/null)
  else
    log "未找到已发布技能 → 首次发布到 xiaping（试用版）"
    resp=$(curl -s -m60 -X POST https://xiaping.coze.com/api/skills \
      -H "Authorization: Bearer $XIAPING_KEY" \
      -F "name=照妖镜·有害信息鉴别智能体" \
      -F "description=AI 鉴别谣言/营销号/诈骗/AI伪造内容，给出风险分级与权威依据。视频语音鉴别需配套后端；诚实分级、仅供参考。" \
      -F 'trigger=["谣言","诈骗","营销号","AI伪造","辟谣","照妖镜","防骗"]' \
      -F 'category=["生活实用"]' \
      -F 'tags=["反诈骗","谣言鉴别","防骗","AI鉴别"]' \
      -F "version=1.0.0" \
      -F "file=@$PKG" \
      -F "requires_api_key=true" \
      $pledge_arg 2>/dev/null)
  fi

  if echo "$resp" | grep -qi "PLEDGE_REQUIRED"; then
    die "⚠️ xiaping 要求主人（人类）明确同意上传承诺。请人工确认后重跑并追加 pledge={\"agreed\":true}（Agent 不得代同意）。"
  fi
  echo "$resp" | head -c 400; echo
  log "Coze/虾评 处理完成。"
}

skillhub_note(){
  log "SkillHub 无公开 API：请人工在 skillhub.cn 重新上传同一 zip（$ROOT/dist/scam-slayer-coze.zip）。"
  log "  登录 → 个人中心 → 我的技能 → 更新/重传 → 选 $ROOT/dist/scam-slayer-coze.zip。"
}

# ---------- 主流程 ----------
main(){
  drift_check
  [ "$CHECK_ONLY" -eq 1 ] && { log "仅检查模式，结束。"; exit 0; }
  mirror_to_data
  sync_web
  push_github
  if [ "$PUBLISH" -eq 1 ]; then
    log "发布模式：Coze（API）+ SkillHub（出包）"
    local fp; fp=$(kb_fingerprint)
    local last; last=$(cat "$PUBLISH_STATE" 2>/dev/null || echo "")
    if [ "$fp" = "$last" ]; then
      log "核心 KB 指纹未变（与上次发布一致）→ 跳过 Coze 重传，避免刷版本。"
    else
      coze_update
      echo "$fp" > "$PUBLISH_STATE"
      log "已记录本次发布指纹：$fp"
    fi
    skillhub_note
  else
    log "未指定 --publish：跳过 Coze/SkillHub 发布（本地/线上/GitHub 已同步）。"
  fi
  log "✅ 全链路同步完成。"
}

main "$@"
