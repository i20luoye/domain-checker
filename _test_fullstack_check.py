"""端到端全栈测试：API GET / POST + 静态 index.html + 新字段渲染校验"""
import urllib.request
import json

BASE = 'http://127.0.0.1:18780'

# 1. GET /api/check（健康检查）
r = urllib.request.urlopen(f'{BASE}/api/check', timeout=10)
data = json.loads(r.read().decode())
print('1) GET /api/check ->', r.status, '| bootstrap:', data.get('bootstrap_loaded'))
assert r.status == 200
assert 'bootstrap_loaded' in data

# 2. POST /api/check（批量查询）
body = json.dumps({'domains': ['google.com', 'qzqx.com', 'a.io', 'b.cn', 'example.org']}).encode()
req = urllib.request.Request(f'{BASE}/api/check', data=body, headers={'Content-Type': 'application/json'})
r = urllib.request.urlopen(req, timeout=15)
data = json.loads(r.read().decode())
print('2) POST /api/check ->', r.status, '| results:', len(data['results']))
assert r.status == 200
for res in data['results']:
    assert 'domain' in res
    assert 'status' in res
    assert 'confidence' in res
    assert 'is_whois' in res
    assert 'sources_ok' in res
    assert 'sources_total' in res
    assert 'checked_at' in res
    print(f'   {res["domain"]:18s} status={res["status"]:10s} conf={res["confidence"]:10s} '
          f'method={res["method"]:10s} is_whois={res["is_whois"]} '
          f'ok/total={res["sources_ok"]}/{res["sources_total"]} premium={res["premium"]}')

# 3. GET /index.html（静态资源）
r = urllib.request.urlopen(f'{BASE}/index.html', timeout=5)
content = r.read().decode()
print('3) GET /index.html ->', r.status, '| size:', len(content))
assert r.status == 200
# 验证新加的功能代码
checks = {
    'API 状态徽章 DOM': 'id="apiStatus"' in content,
    'API 状态样式': '.api-status-ok' in content,
    '置信度样式': '.conf-VERY_HIGH' in content,
    '置信度列': '<th>置信度</th>' in content,
    'sources_ok 渲染': 'sources_ok' in content,
    'is_whois 渲染': 'is_whois' in content,
    'checked_at 渲染': 'checked_at' in content,
    'pingApi 函数': 'function pingApi' in content,
    'formatRelativeTime 函数': 'formatRelativeTime' in content,
    'CSV 新列': '置信度' in content and '数据源' in content,
}
all_ok = True
for name, ok in checks.items():
    print(f'   [{"OK" if ok else "FAIL"}] {name}')
    if not ok: all_ok = False
assert all_ok, '静态资源校验失败'

print()
print('=' * 60)
print('全栈联调全部通过 ✅')
print('=' * 60)
