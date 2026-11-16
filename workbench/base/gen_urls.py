"""解析剩余依赖的 wheel 下载地址（阿里云镜像）。

对 requirements-rest.txt 中每个 name==ver：
1. 抓取 simple 索引页（urllib 走环境代理，已验证可用）
2. 按兼容性优先级选 wheel：cp313-win_amd64 > cp313-abi3-win_amd64 > cp3-abi3-win_amd64 > py3-none-any > cp312 兜底
3. 输出 url_list.txt（url<TAB>文件名），供 curl 断点续传下载
"""
import re
import urllib.request
from html.parser import HTMLParser

BASE = r"E:\Workspace\TestingTable\workbench\base"
REQ = BASE + r"\requirements-rest.txt"
OUT = BASE + r"\url_list.txt"

INDEX = "https://mirrors.aliyun.com/pypi/simple/{name}/"

class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
    def handle_starttag(self, tag, attrs):
        if tag == "a":
            d = dict(attrs)
            if "href" in d:
                self.links.append((d["href"], d.get("data-requires-python", "")))

def wheel_priority(fname):
    """返回 (优先级, 是否可用)。越大越优先。支持 sdist 兜底。"""
    f = fname.lower()
    if f.endswith(".whl"):
        prio = 0
        ok = False
        if "cp313-cp313-win_amd64" in f:
            prio, ok = 50, True
        elif "cp313-abi3-win_amd64" in f:
            prio, ok = 40, True
        elif "-abi3-win_amd64" in f:
            prio, ok = 30, True
        elif "py3-none-any" in f or "py2.py3-none-any" in f:
            prio, ok = 20, True
        elif "py3-none-win_amd64" in f:
            prio, ok = 18, True
        return prio, ok
    if f.endswith(".tar.gz") or f.endswith(".zip"):
        return 5, True  # sdist 兜底：纯 Python 包 pip 可本地构建
    return 0, False

def main():
    lines = [l.strip() for l in open(REQ, encoding="utf-8") if l.strip() and not l.startswith("#")]
    results = []
    failures = []
    for line in lines:
        name = re.split(r"[=<>!~\[ ]", line)[0]
        ver_m = re.search(r"==([^;\s]+)", line)
        ver = ver_m.group(1) if ver_m else None
        norm = re.sub(r"[-_.]+", "-", name).lower()  # PEP 503 归一化
        url = INDEX.format(name=norm)
        try:
            html = urllib.request.urlopen(url, timeout=30).read().decode("utf-8", "ignore")
        except Exception as e:
            failures.append((line, f"index fetch fail: {e}"))
            continue
        p = LinkParser()
        p.feed(html)
        best = None
        for href, reqpy in p.links:
            fname = href.split("#")[0].rsplit("/", 1)[-1]
            # 版本过滤：wheel/sdist 文件名以 {name}_{ver}- 开头（- 和 . 写作 _）
            if ver:
                prefix = re.sub(r"[-_.]+", "_", name.lower()) + "-" + ver + "-"
                if not fname.lower().startswith(prefix):
                    continue
            prio, ok = wheel_priority(fname)
            if ok and (best is None or prio > best[0]):
                full = href if href.startswith("http") else urllib.request.urljoin(url, href)
                best = (prio, fname, full)
        if best:
            results.append((line, best[1], best[2]))
            print(f"OK   {line:45s} -> {best[1]}")
        else:
            failures.append((line, "no compatible wheel"))
            print(f"FAIL {line:45s} -> 无兼容 wheel")

    with open(OUT, "w", encoding="utf-8") as f:
        for line, fname, full in results:
            f.write(f"{full}\t{fname}\n")
    print(f"\n解析成功 {len(results)} 个，失败 {len(failures)} 个")
    for line, why in failures:
        print(f"  失败: {line} ({why})")

if __name__ == "__main__":
    main()
