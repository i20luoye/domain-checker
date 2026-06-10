"""多 Provider 链路实测：domain_checker.py 的 check_domain_with_chain
零配置链路：rdap + godaddypublic + botoi
"""
import asyncio
import sys
import time
sys.path.insert(0, '.')
import importlib.util
spec = importlib.util.spec_from_file_location('dc', 'domain_checker.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

# 关闭 DNS 预筛，纯测 RDAP + GoDaddy 公共 + Botoi 链路
m.DNS_PREFILTER_ENABLED = False
m.PROVIDER_BREAKER_ERROR_THRESHOLD = 100  # 测试环境放宽熔断

# 零配置链路（不用 Porkbun/Domainr/WhoisFreaks）
CHAIN = ['rdap', 'godaddypublic', 'botoi']

# 测试集：覆盖已注册/可注册/可注册可能溢价/多 TLD
TEST_DOMAINS = [
    # .com - 已注册大站
    'google.com', 'github.com', 'openai.com', 'stackoverflow.com',
    # .com - 随机可注册
    'qzqxqzzq.com', 'qwerty12345x.com', 'zxcvbnmqwer123.com',
    # .io
    'github.io', 'a.io', 'zqzxqzx.io',
    # .org
    'python.org', 'qzqxq.org',
    # .cn
    'baidu.cn', 'cnnic.cn',
    # .ai
    'openai.ai', 'qzqx.ai',
]

# 检查环境变量（看看是不是配置了真实 Key）
print('=== 环境变量检查 ===')
print(f'  GODADDY_KEY:     {"<set>" if m.GODADDY_KEY else "<未配置>"}')
print(f'  PORKBUN_KEY:     {"<set>" if m.PORKBUN_KEY else "<未配置>"}')
print(f'  DOMAINR_KEY:     {"<set>" if m.DOMAINR_RAPIDAPI_KEY else "<未配置>"}')
print(f'  WHOISFREAKS_KEY: {"<set>" if m.WHOISFREAKS_KEY else "<未配置>"}')
print(f'  测试链路: {CHAIN}')
print(f'  测试域名: {len(TEST_DOMAINS)} 个')
print()


async def run():
    import aiohttp
    connector = aiohttp.TCPConnector(limit=10, ssl=False)
    session = aiohttp.ClientSession(connector=connector)
    sem = asyncio.Semaphore(5)
    t0 = time.time()

    print(f'{"域名":30s} {"状态":10s} {"方法":25s} {"溢价":8s} {"原因":20s} {"用时"}')
    print('-' * 110)
    results = []
    for d in TEST_DOMAINS:
        suffix = d.rsplit('.', 1)[1]
        ts = time.time()
        r = await m.check_domain_with_chain(session, d, suffix, sem, chain=CHAIN)
        cost = time.time() - ts
        results.append(r)
        print(f'{d:30s} {r["status"]:10s} {r["method"]:25s} {str(r.get("premium")):8s} '
              f'{(r.get("premiumReason") or "")[:20]:20s} {cost:.2f}s')
    total = time.time() - t0
    print('-' * 110)
    print(f'总耗时: {total:.2f}s · 平均: {total/len(TEST_DOMAINS):.2f}s/域名')

    # 统计
    taken = sum(1 for r in results if r['status'] == 'taken')
    avail = sum(1 for r in results if r['status'] == 'available')
    error = sum(1 for r in results if r['status'] == 'error')
    premium = sum(1 for r in results if r.get('premium'))
    print(f'\n统计: taken={taken}  available={avail}  error={error}  premium={premium}')

    # 按 provider 命中率统计
    method_counts = {}
    for r in results:
        m_name = r['method'].split('(')[0] if '(' in r['method'] else r['method']
        method_counts[m_name] = method_counts.get(m_name, 0) + 1
    print(f'\n链路命中分布:')
    for k, v in sorted(method_counts.items(), key=lambda x: -x[1]):
        print(f'  {k:20s} {v} 次')

    await session.close()


asyncio.run(run())
