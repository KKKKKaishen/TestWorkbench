#!/bin/bash
# 等 wb-pip 安装结束后重启 wb-cloud 并做健康检查（服务器侧自守，日志: /root/post_pip.log）
exec > /root/post_pip.log 2>&1
for i in $(seq 1 180); do
  st=$(systemctl is-active wb-pip 2>/dev/null)
  if [ "$st" != "active" ]; then
    echo "$(date '+%F %T') wb-pip 已结束（状态 $st，第 $i 次检查）"
    break
  fi
  sleep 30
done
echo "$(date '+%F %T') 关键包检查："
/opt/wb-cloud/app/venv/bin/pip list 2>/dev/null | grep -iE 'chromadb|^langchain |langchain-core|unstructured' | head -6
echo "$(date '+%F %T') 重启 wb-cloud..."
systemctl restart wb-cloud
sleep 8
echo "服务状态: $(systemctl is-active wb-cloud)"
echo "$(date '+%F %T') 健康检查："
curl -s -m 8 http://127.0.0.1:8902/wb/sync/ping -H 'X-Sync-Token: 64dda18ccb108375ed19291450e60ef4282b286c9b9d2c29'
echo
curl -s -m 8 -o /dev/null -w "app:%{http_code}\n" http://127.0.0.1:8902/app/
curl -s -m 8 -o /dev/null -w "login-endpoint:%{http_code}\n" -X POST http://127.0.0.1:8902/wb/auth/login -H 'Content-Type: application/json' -d '{"username":"cyu","password":"wrong"}'
journalctl -u wb-cloud --since "1 min ago" --no-pager | grep -E 'ERROR|Traceback|Application startup|Uvicorn running' | tail -6
free -m | head -2
echo "$(date '+%F %T') DONE"
