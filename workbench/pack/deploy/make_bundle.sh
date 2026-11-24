#!/usr/bin/env bash
# Cyu工作台云同步中枢 —— 服务器端打包脚本（本机执行）
# 产出 pack/deploy/dist/wb-cloud-bundle.tar.gz，上传到服务器后执行 deploy_server.sh
set -e
cd "$(dirname "$0")"
BASE=../../base
STAGE=staging/app
rm -rf staging
mkdir -p $STAGE

# 代码：顶层 py + 四个包目录（排除 __pycache__ / 测试 / 重资源）
for f in $BASE/*.py; do
  case "$(basename $f)" in
    server_entry.py|_mock_openai.py|_sync_e2e.py|_sync_dbg.py|\
_hotfix_cloud.py|_fix_local_triggers.py|_seed_rowver.py|_migrate_dev_to_cloud.py|_push_via_api.py) continue;;
  esac
  cp "$f" $STAGE/
done
for d in models services utils api schemas prompts workflows; do
  mkdir -p $STAGE/$d
  find $BASE/$d -name "*.py" -not -path "*__pycache__*" -exec cp --parents {} $STAGE/$d/ \; 2>/dev/null || \
    find $BASE/$d -name "*.py" -not -path "*__pycache__*" | while read p; do
      rel=${p#$BASE/}; mkdir -p "$STAGE/$(dirname $rel)"; cp "$p" "$STAGE/$rel"
    done
done

# 前端构建产物
mkdir -p $STAGE/web_dist
cp -r ../../web/dist/* $STAGE/web_dist/

# 部署物料（统一转 LF，避免 Windows CRLF 在服务器 bash 上炸掉）
mkdir -p dist
for f in requirements-lite.txt deploy_server.sh nginx-wb.conf wb-cloud.service; do
  tr -d '\r' < "$f" > "staging/$f"
done
tar czf dist/wb-cloud-bundle.tar.gz -C staging .
echo "打包完成: $(ls -lh dist/wb-cloud-bundle.tar.gz | awk '{print $5}')  -> pack/deploy/dist/wb-cloud-bundle.tar.gz"
