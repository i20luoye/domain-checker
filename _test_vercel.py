"""本地测试 Vercel 风格的 serverless endpoint
启动一个 HTTP server 模拟 Vercel 的 serverless function 调用方式
"""
import sys, json
sys.path.insert(0, "api")
from http.server import HTTPServer, BaseHTTPRequestHandler
from check import check_domain
import concurrent.futures, time

class TestHandler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length))
        domains = body.get("domains", [])
        print(f"\n>>> 收到 {len(domains)} 个域名查询请求")

        start = time.time()
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            future_map = {pool.submit(check_domain, d): d for d in domains}
            for future in concurrent.futures.as_completed(future_map):
                try:
                    results.append(future.result())
                except Exception as e:
                    results.append({"domain": future_map[future], "status": "error", "detail": str(e)})
        elapsed = time.time() - start
        print(f"<<< 完成: {elapsed:.1f}s")

        payload = json.dumps({"results": results, "elapsed_s": elapsed}, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 18765), TestHandler)
    print("测试服务器启动: http://127.0.0.1:18765")
    server.serve_forever()
