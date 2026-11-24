"""核对云端 .env 当前数据库密码与服务状态。"""
import paramiko

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect("8.133.254.215", username="root", password="Yy990216@", timeout=15)

def run(cmd, timeout=60):
    _, out, err = cli.exec_command(cmd, timeout=timeout)
    out.channel.recv_exit_status()
    o, e = out.read().decode(), err.read().decode()
    print(f"$ {cmd[:100]}\n{o[-1200:]}" + (f"[stderr] {e[-300:]}" if e.strip() else "") + "\n")
    return o

run("grep DATABASE_URL /opt/wb-cloud/app/data/.env")
run("cd /opt/wb-cloud/app && venv/bin/python -c \"\n"
    "import pymysql, re\n"
    "from urllib.parse import unquote\n"
    "url = [l.split('=',1)[1].strip() for l in open('data/.env') if l.startswith('DATABASE_URL')][0]\n"
    "m = re.match(r'mysql\\+pymysql://([^:]+):([^@]+)@([^:/]+):(\\d+)/([^?]+)', url)\n"
    "c = pymysql.connect(host='localhost', user=m.group(1), password=unquote(m.group(2)), database=m.group(5), charset='utf8mb4')\n"
    "cur = c.cursor()\n"
    "cur.execute('SELECT id, filename, status, uploaded_at FROM knowledge_files ORDER BY uploaded_at DESC LIMIT 5')\n"
    "for r in cur.fetchall(): print(r)\n"
    "print('---bases---')\n"
    "cur.execute('SELECT id, name, description FROM knowledge_bases LIMIT 5')\n"
    "for r in cur.fetchall(): print(r)\n"
    "c.close()\"")
run("systemctl is-active wb-cloud && curl -s -m 5 -X POST https://wb.ka1shen.cn/wb/auth/login -H 'Content-Type: application/json' -d '{\"username\":\"cyu\",\"password\":\"xx19980304cky\"}' -o /dev/null -w 'login:%{http_code}\\n'")
cli.close()
