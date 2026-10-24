#!/usr/bin/env bash
# 从完整仓库递归补齐目标仓库缺失的 Git 对象。
#
# 典型用途：已安装副本（如 C:\Program Files\AzurPilot）的对象库可能因安装包
# 裁剪历史而残缺，表现为 git fetch 正常退出但仍打印 “Could not read <sha>”、
# commit-graph 写入失败、rev-list 报 “Failed to traverse parents”。
# 缺的是旧提交的 parent 及其 tree/blob，GitHub 默认不允许按任意 SHA 取对象，
# 因此从本地完整克隆逐个对象搬运。哈希写入后可自校验，不会引入错内容。
#
# 用法: fill_installed_objects.sh <源仓库> <目标仓库> [最大轮数]
# 退出码 0 表示已无缺失对象；1 表示仍有缺失或源仓库也缺。
set -u

SRC="$1"
DST="$2"
MAX_ROUNDS="${3:-40}"

cd "$DST" || exit 1

for round in $(seq 1 "$MAX_ROUNDS"); do
  # 收集本轮所有缺失对象（commit / tree / blob 都要补）
  missing=$(git fsck --connectivity-only --no-dangling 2>/dev/null \
            | awk '/^missing (commit|tree|blob) /{print $3}' | sort -u)

  if [ -z "$missing" ]; then
    echo "第 ${round} 轮：无缺失对象，完成"
    exit 0
  fi

  count=$(echo "$missing" | wc -l | tr -d ' ')
  echo "第 ${round} 轮：待补 ${count} 个对象"

  failed=0
  while read -r obj; do
    [ -z "$obj" ] && continue
    # 判断对象类型，再用对应类型写入，保证 SHA 可校验
    type=$(cd "$SRC" && git cat-file -t "$obj" 2>/dev/null)
    if [ -z "$type" ]; then
      echo "  ⚠️ 源仓库也没有 $obj"
      failed=$((failed + 1))
      continue
    fi
    (cd "$SRC" && git cat-file "$type" "$obj") > /tmp/fill_obj.raw 2>/dev/null || {
      failed=$((failed + 1)); continue; }
    new=$(git hash-object -t "$type" -w /tmp/fill_obj.raw)
    if [ "$new" = "$obj" ]; then
      echo "  ✅ $type ${obj:0:12}"
    else
      echo "  ❌ $type ${obj:0:12} -> $new（内容不一致）"
      failed=$((failed + 1))
    fi
  done <<< "$missing"
  rm -f /tmp/fill_obj.raw

  if [ "$failed" -gt 0 ]; then
    echo "本轮有 ${failed} 个对象无法补齐，中止"
    exit 1
  fi
done

echo "达到最大轮数 ${MAX_ROUNDS}，仍有缺失"
exit 1
