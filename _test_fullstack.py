"""完整模拟 Vercel 部署：api/check.py + public/index.html
FullStackHandler 继承 ApiHandler，保留其 do_GET/do_POST/do_OPTIONS，覆写它们添加 /api 之外的静态路由。
"""
import http.server
import socketserver
import os
import sys
import importlib.util
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

# 加载 check.py
sys.path.insert(0, os.path.join(ROOT, 'api'))
spec = importlib.util.spec_from_file_location('check', os.path.join(ROOT, 'api', 'check.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
ApiHandler = m.handler


class FullStackHandler(ApiHandler):
    """继承 ApiHandler，/api/* 走父类，其它路径走 public/"""

    def log_message(self, *a, **kw): pass

    def _serve_static(self):
        path = self.path.split('?')[0]
        if path in ('/', ''): path = '/index.html'
        rel = path.lstrip('/')
        full = os.path.normpath(os.path.join(ROOT, 'public', rel))
        if not full.startswith(os.path.join(ROOT, 'public')) or not os.path.isfile(full):
            self.send_error(404)
            return
        ext = os.path.splitext(full)[1].lower()
        ct = ('text/html; charset=utf-8' if ext == '.html' else
              'text/css; charset=utf-8' if ext == '.css' else
              'application/javascript; charset=utf-8' if ext == '.js' else
              'image/svg+xml' if ext == '.svg' else
              'application/octet-stream')
        try:
            data = open(full, 'rb').read()
            self.send_response(200)
            self.send_header('Content-Type', ct)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self.send_error(500, str(e))

    def _route(self):
        if self.path.startswith('/api/'):
            method = self.command
            try:
                if method == 'OPTIONS':
                    ApiHandler.do_OPTIONS(self)
                elif method == 'GET':
                    ApiHandler.do_GET(self)
                elif method == 'POST':
                    ApiHandler.do_POST(self)
                else:
                    self.send_error(405)
            except Exception as e:
                print('[api error]', method, self.path, e, file=sys.stderr)
                traceback.print_exc()
                try:
                    self._send_json({'error': str(e)}, 500)
                except:
                    self.send_error(500, str(e))
            return
        self._serve_static()

    def do_GET(self):    self._route()
    def do_POST(self):   self._route()
    def do_OPTIONS(self): self._route()


PORT = 18780
print(f'完整模拟服务器启动: http://127.0.0.1:{PORT}/')
print(f'  静态首页:  http://127.0.0.1:{PORT}/index.html')
print(f'  API 健康:  http://127.0.0.1:{PORT}/api/check (GET)')

with socketserver.ThreadingTCPServer(('127.0.0.1', PORT), FullStackHandler) as srv:
    srv.allow_reuse_address = True
    srv.serve_forever()
