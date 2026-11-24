#!/usr/bin/env bash
# Cyu工作台云同步中枢 —— 服务器端部署脚本（Ubuntu/Debian，root 执行）
# 前置：wb-cloud-bundle.tar.gz 已上传到 /root/
set -e
INSTALL_DIR=/opt/wb-cloud
SERVICE_NAME=wb-cloud
DOMAIN=wb.ka1shen.cn
PORT=8902

echo "== 1. 解包 =="
mkdir -p $INSTALL_DIR
tar xzf /root/wb-cloud-bundle.tar.gz -C $INSTALL_DIR

echo "== 2. MySQL =="
MYSQL_ROOT_PWD="${MYSQL_ROOT_PWD:?需要 MYSQL_ROOT_PWD 环境变量}"
# 复用已有 .env 的数据库密码（重复部署时不更换，避免与 MySQL 用户失配）
DB_PASS=$(grep -oP '(?<=DATABASE_URL=mysql\+pymysql://wbcloud:)[^@]+' $INSTALL_DIR/app/data/.env 2>/dev/null || true)
if [ -z "$DB_PASS" ]; then
  DB_PASS=$(openssl rand -hex 16)
fi
mysql -uroot -p"$MYSQL_ROOT_PWD" -e "CREATE DATABASE IF NOT EXISTS workbench_cloud CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;" 2>/dev/null
mysql -uroot -p"$MYSQL_ROOT_PWD" -e "CREATE USER IF NOT EXISTS 'wbcloud'@'localhost' IDENTIFIED BY '$DB_PASS'; ALTER USER 'wbcloud'@'localhost' IDENTIFIED BY '$DB_PASS';" 2>/dev/null
mysql -uroot -p"$MYSQL_ROOT_PWD" -e "GRANT ALL PRIVILEGES ON workbench_cloud.* TO 'wbcloud'@'localhost'; FLUSH PRIVILEGES;" 2>/dev/null
# 存量实例已按低内存配置（buffer_pool=128M），此处不再改全局配置重启

echo "== 3. Python 环境 =="
python3 --version
apt-get install -y -qq python3-venv >/dev/null
cd $INSTALL_DIR/app
python3 -m venv venv
./venv/bin/pip install -q --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
./venv/bin/pip install -q -r ../requirements-lite.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

echo "== 4. 配置 =="
# 复用已有同步令牌 / 会话密钥（重复部署不换，客户端无需重新配置）
OLD_TOKEN=$(grep -oP '(?<=WB_SYNC_TOKEN=).*' $INSTALL_DIR/app/data/.env 2>/dev/null || true)
OLD_SESSION=$(grep -oP '(?<=SESSION_SECRET_KEY=).*' $INSTALL_DIR/app/data/.env 2>/dev/null || true)
SYNC_TOKEN=${OLD_TOKEN:-$(openssl rand -hex 24)}
SESSION_KEY=${OLD_SESSION:-$(openssl rand -hex 32)}
mkdir -p $INSTALL_DIR/app/data/uploads $INSTALL_DIR/app/data/logs
cat > $INSTALL_DIR/app/data/.env <<EOF
DATABASE_URL=mysql+pymysql://wbcloud:$DB_PASS@localhost:3306/workbench_cloud?charset=utf8mb4
APP_ENV=production
SESSION_SECRET_KEY=$SESSION_KEY
WB_SYNC_ROLE=cloud
WB_SYNC_TOKEN=$SYNC_TOKEN
WB_LITE=1
UPLOAD_DIR=$INSTALL_DIR/app/data/uploads
WORKBENCH_LOG_DIR=$INSTALL_DIR/app/data/logs
EOF
chmod 600 $INSTALL_DIR/app/data/.env
echo "  同步访问令牌(客户端配置用): $SYNC_TOKEN"

echo "== 5. systemd =="
sed -i "s|__INSTALL_DIR__|$INSTALL_DIR|g" /root/../$INSTALL_DIR/wb-cloud.service 2>/dev/null || true
sed -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" -e "s|__SERVICE__|$SERVICE_NAME|g" $INSTALL_DIR/wb-cloud.service > /etc/systemd/system/$SERVICE_NAME.service
systemctl daemon-reload
systemctl enable $SERVICE_NAME
systemctl restart $SERVICE_NAME   # 强制重启以加载最新 .env（enable --now 对已运行服务不生效）
sleep 3
systemctl --no-pager -l status $SERVICE_NAME | head -8 || true
curl -s -m 5 http://127.0.0.1:$PORT/wb/sync/ping -H "X-Sync-Token: $SYNC_TOKEN" && echo " <- 本机 ping OK"

echo "== 6. Nginx =="
if ! command -v nginx >/dev/null; then
  apt-get install -y -qq nginx
fi
sed -e "s|__DOMAIN__|$DOMAIN|g" -e "s|__PORT__|$PORT|g" $INSTALL_DIR/nginx-wb.conf > /etc/nginx/sites-available/$SERVICE_NAME
ln -sf /etc/nginx/sites-available/$SERVICE_NAME /etc/nginx/sites-enabled/$SERVICE_NAME
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo "== 完成 =="
echo "  HTTP:    http://$DOMAIN (DNS 生效后)  http://$(hostname -I | awk '{print $1}'):$PORT"
echo "  同步令牌: $SYNC_TOKEN （已写入 $INSTALL_DIR/app/data/.env）"
echo "  HTTPS:   等 DNS A 记录生效后执行:"
echo "           apt-get install -y certbot python3-certbot-nginx && certbot --nginx -d $DOMAIN"
