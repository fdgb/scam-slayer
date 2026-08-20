#!/usr/bin/env bash
#
# set_form.sh — 一键把「匿名提交表单」链接写进 README 表单占位
#
# 用法:
#   bash scripts/set_form.sh <表单链接>
#   bash scripts/set_form.sh https://wj.qq.com/s/xxxxxx
#
# 作用: 把 README 顶部「📝 匿名提交表单：」占位行替换为真实链接，并自动提交。
#       之后跑 weekly_sync.sh 推送即可生效。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
README="$ROOT/README.md"
URL="${1:-}"

if [ -z "$URL" ]; then
  echo "用法: bash scripts/set_form.sh <表单链接>"
  echo "示例: bash scripts/set_form.sh https://wj.qq.com/s/abc123"
  exit 1
fi

if ! printf '%s' "$URL" | grep -qE '^https?://'; then
  echo "❌ 链接需以 http:// 或 https:// 开头"
  exit 1
fi

python3 - "$README" "$URL" <<'PY'
import sys
p, url = sys.argv[1], sys.argv[2]
lines = open(p, encoding="utf-8").read().splitlines(keepends=True)
done = False
for i, ln in enumerate(lines):
    if ln.startswith("📝 匿名提交表单："):
        lines[i] = f"📝 匿名提交表单：{url}\n"
        done = True
        break
if done:
    open(p, "w", encoding="utf-8").writelines(lines)
    print("✅ 已把表单链接写入 README 顶部")
else:
    print("⚠️ 未找到「📝 匿名提交表单：」行，请手动在 README 顶部粘贴链接")
PY

cd "$ROOT"
git add README.md
if git diff --cached --quiet; then
  echo "（README 无变化，未提交）"
else
  git commit -q -m "docs: set anonymous submission form link"
  echo "已提交本地（push 由 weekly_sync.sh 完成）"
fi
