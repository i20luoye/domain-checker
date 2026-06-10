"""端到端测试：启动真实 HTTP server，复用 api/check.py 的 handler"""
import sys, json, time, threading
sys.path.insert(0, "api")
from http.server import HTTPServer
from check import handler


class TestServer(HTTPServer):
    allow_reuse_address = True


server = TestServer(("127.0.0.1", 18766), handler)
print("测试服务器启动: http://127.0.0.1:18766")
server_thread = threading.Thread(target=server.serve_forever, daemon=True)
server_thread.start()

import urllib.request

def post(domains: list) -> dict:
    body = json.dumps({"domains": domains}).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:18766/",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))

def get_health() -> dict:
    with urllib.request.urlopen("http://127.0.0.1:18766/") as resp:
        return json.loads(resp.read().decode("utf-8"))


# 健康检查
print("=== GET / ===")
health = get_health()
print(f"bootstrap 加载: {health.get('bootstrap_loaded')} TLD")
print(f"样例 TLD: {health.get('sample_tlds')}")

# 关键测试
test_domains = [
    "google.com",
    "qzqxqzzq.com",
    "python.org",
    "github.io",
    "aieo.cn",
    "openai.com",
    "example.com",
    "stackoverflow.com",
    "reddit.com",
    "notexist-qzq1234.com",
    "cloudflare.com",
    "rust-lang.org",
]

print(f"\n=== POST / (n={len(test_domains)}) ===")
start = time.time()
r = post(test_domains)
elapsed = time.time() - start
print(f"总耗时: {elapsed:.2f}s | bootstrap 加载: {r.get('bootstrap_loaded')} TLD\n")
print(f"{'域名':<26} {'状态':<10} {'置信度':<12} {'溢价':<5} {'方法':<10}")
print("-" * 70)
for x in r.get("results", []):
    print(f"{x['domain']:<26} {x['status']:<10} {x['confidence']:<12} {str(x['premium']):<5} {x['method']:<10}")

server.shutdown()
