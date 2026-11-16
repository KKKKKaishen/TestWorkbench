#!/bin/bash
# 断点续传下载 wheel：用法 ./download_wheels.sh <list文件> [起始行] [结束行]
LIST="$1"; FROM="${2:-1}"; TO="${3:-100000000}"
WHEELS="/e/Workspace/TestingTable/workbench/base/wheels"
mkdir -p "$WHEELS"
n=$FROM
tail -n +"$FROM" "$LIST" | head -n $((TO - FROM + 1)) | while IFS=$'\t' read -r url fname; do
  [ -z "$url" ] && continue
  n=$((n+1))
  if [ -f "$WHEELS/$fname.ok" ]; then
    echo "[skip] $fname"
    continue
  fi
  echo "[down] $fname"
  curl -sSL -C - --retry 3 --retry-delay 2 -m 500 -o "$WHEELS/$fname" "$url" && touch "$WHEELS/$fname.ok" && echo "  ok" || {
    # 可能已下载完成(416)或真失败：再试一次短下载验证
    curl -sSL -C - --retry 2 -m 120 -o "$WHEELS/$fname" "$url" && touch "$WHEELS/$fname.ok" && echo "  ok(重试)" || echo "  FAIL $fname"
  }
done
echo "DONE_RANGE=$FROM-$TO"
