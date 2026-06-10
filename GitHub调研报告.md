# GitHub 优秀开源项目深度分析报告

> 调研时间：2026-06-10
> 分析项目：sithulaka/DomainChecker · saidutt46/domain-check · l3lackcurtains/beast-domain-checker

---

## 目录

1. [sithulaka/DomainChecker (Python)](#1-sithulakadomainchecker-python)
2. [saidutt46/domain-check (Rust/MCP)](#2-saidutt46domain-check-rustmcp)
3. [l3lackcurtains/beast-domain-checker (JS/Node.js)](#3-l3lackcurtainsbeast-domain-checker-jsnodejs)
4. [横向对比](#4-横向对比)
5. [对我们的借鉴价值](#5-对我们的借鉴价值)
6. [关键源码摘录](#6-关键源码摘录)

---

## 1. sithulaka/DomainChecker (Python)

**仓库**: https://github.com/sithulaka/DomainChecker  
**语言**: Python 100%  
**Star**: 少众但架构设计优秀

### 1.1 架构：三层漏斗过滤模型

```
第一层: WHOIS 查询 (python-whois)
  ├─ registrar, creation_date, expiration_date, name_servers 字段检测
  └─ "not found"/"no match" → 判为 AVAILABLE
        │
        ↓ (仅 WHOIS 判为可用时进入)
第二层: DNS 记录检测 (dnspython)
  ├─ 检测 A, AAAA, MX, NS, CNAME, SOA 记录
  └─ 没有任何记录 → 进一步验证
        │
        ↓ (DNS 检测通过时进入)
第三层: HTTP/HTTPS 响应检测 (requests)
  ├─ HEAD 请求到 https:// 和 http://
  └─ 有响应 → RESTRICTED/PREMIUM；无响应 → AVAILABLE
        │
        ↓ (可选)
第四层: Namecheap API 验证
  ├─ XML 接口 namecheap.domains.check
  └─ ispremiumname 字段 → PREMIUM 检测
```

### 1.2 置信度评分系统

不给出二元结果，而是置信度等级：

| API结果 | WHOIS | DNS | HTTP | 最终判定 | 置信度 |
|---------|-------|-----|------|---------|--------|
| AVAILABLE | — | — | — | AVAILABLE | VERY HIGH |
| PREMIUM | — | — | — | PREMIUM | HIGH |
| TAKEN | — | — | — | TAKEN | VERY HIGH |
| — | 不可用 | — | — | TAKEN | HIGH |
| — | 可用 | 无 | 无 | AVAILABLE | HIGH |
| — | 可用 | NS/SOA | 无 | POSSIBLY AVAILABLE | MEDIUM |
| — | 可用 | A/MX | — | RESTRICTED/PREMIUM | MEDIUM |
| — | 可用 | — | 有响应 | RESTRICTED/PREMIUM | LOW |

### 1.3 Namecheap API 溢价检测

通过 Namecheap XML API 的 `ispremiumname` 字段判断 premium，是目前**最准确且免费**的方案（需要 Namecheap 账户，API 查询不计费）。

---

## 2. saidutt46/domain-check (Rust/MCP)

**仓库**: https://github.com/saidutt46/domain-check  
**语言**: Rust 98%  
**Star**: 284  
**亮点**: IANA Bootstrap 动态路由，1200+ TLD 自动支持

### 2.1 三层架构

```
domain-check/
├── domain-check/          # CLI 前端（binary）
├── domain-check-lib/      # 核心库
│   └── protocols/
│       ├── rdap.rs        # RDAP 客户端
│       ├── whois.rs       # WHOIS 客户端
│       └── registry.rs    # IANA Bootstrap + 缓存
└── domain-check-mcp/      # MCP 服务器
```

### 2.2 IANA Bootstrap 三层缓存（核心亮点）

```
请求 "example.xyz"
        │
        ▼
第1层: 硬编码 32 个 TLD 映射
  ├─ 命中 → 直接返回端点 URL
  └─ 未命中 → 进入第2层
        │
        ▼
第2层: Bootstrap 进程缓存（OnceLock + 24h TTL）
  ├─ 命中且未过期 → 返回缓存端点
  ├─ 负缓存命中 → 返回 BootstrapError
  └─ 未命中或过期 → 拉取 IANA
        │
        ▼
第3层: 动态拉取 IANA Bootstrap
  ├─ GET https://data.iana.org/rdap/dns.json
  ├─ 解析 services 数组
  └─ 支持 1200+ TLD
```

### 2.3 RDAP First, WHOIS Fallback

```
check_domain("example.com")
  ├─ RDAP 优先
  │   ├─ Ok(结果) → 返回
  │   └─ Err → WHOIS 兜底
  └─ WHOIS
      ├─ 18 种可用模式匹配
      ├─ 14 种被占模式计数
      ├─ 短响应启发式（< 50 字符 = 可能可用）
      └─ IANA referral 发现权威 WHOIS 服务器
```

### 2.4 流式结果

```rust
pub fn check_domains_stream(domains: &[String])
    -> Pin<Box<dyn Stream<Item = Result<DomainResult>> + Send + '_>>
{
    let stream = futures_util::stream::iter(domains)
        .map(move |domain| { /* async check */ })
        .buffer_unordered(self.config.concurrency);
    Box::pin(stream)
}
```

### 2.5 语义化错误

12 种错误变体，核心方法：
- `indicates_available()` — 此错误是否暗示域名可注册（如 RDAP 404）
- `is_retryable()` — 网络错误/超时/限流/5xx 可重试

### 2.6 TLD 预设系统

```rust
"startup"   => ["com", "org", "io", "ai", "tech", "app", "dev", "xyz"],
"tech"      => ["io", "ai", "app", "dev", "tech", "cloud", "software", ...],
"creative"  => ["design", "art", "studio", "media", ...],
"finance"   => ["finance", "capital", "fund", "money", ...],
```

---

## 3. l3lackcurtains/beast-domain-checker (JS/Node.js)

**仓库**: https://github.com/l3lackcurtains/beast-domain-checker  
**语言**: TypeScript, Astro 5 + Puppeteer  
**Star**: 较高  
**亮点**: 浏览器自动化查 Namecheap 真实价格

### 3.1 架构

```
src/
├── lib/
│   ├── domainChecker.ts    ← Puppeteer 自动化 Namecheap Beast Mode
│   ├── csvParser.ts        ← CSV/文本解析
│   └── storage.ts          ← JSON 文件存储
├── pages/
│   ├── index.astro         ← 前端页面
│   └── api/
│       ├── check-domains.ts ← POST API
│       └── favorites.ts     ← CRUD API
└── cli.js
```

### 3.2 Puppeteer 工作流

```
用户提交域名列表
  → Puppeteer 打开 Namecheap Beast Mode 页面
  → 上传 CSV 文件（最多 1000 个域名/批）
  → 点击 "Generate" 触发 Namecheap 查询
  → waitForFunction 轮询等待结果渲染
  → 解析每个域名的 article 卡片，提取状态和价格
  → 价格 > $100 = premium
```

### 3.3 溢价检测逻辑

```typescript
if (textLower.includes("add to cart")) {
    const priceValue = parseFloat(price.replace(/[$EUR,\s]/g, ""));
    return { domain, available: true, price, status: priceValue > 100 ? "premium" : "available" };
}
```

---

## 4. 横向对比

| 维度 | DomainChecker (Python) | domain-check (Rust) | beast-domain-checker (JS) |
|---|---|---|---|
| **检测协议** | WHOIS → DNS → HTTP → API | RDAP → WHOIS | Namecheap 浏览器自动化 |
| **并发模型** | threading.Queue (5线程) | tokio::Semaphore (1-100) | Puppeteer 单进程 |
| **TLD 覆盖** | 用户配置文件 | **1200+ (IANA Bootstrap)** | 依赖 Namecheap |
| **Premium检测** | **Namecheap ispremiumname** | ❌ 不支持 | 价格阈值 $100 |
| **输出模式** | 批量 CSV | **流式 + 批量** | 流式日志 + 批量 |
| **结果置信度** | VERY HIGH / HIGH / MEDIUM / LOW | Some/Some/None | binary + premium |
| **部署** | pip install | cargo install | Docker Compose |
| **主要局限** | 速度慢、线程少 | 无价格信息 | 依赖 Namecheap 页面 |

---

## 5. 对我们的借鉴价值

### 5.1 直接拿来用的架构

| 项目 | 借鉴点 | 在我们的代码中对应 |
|---|---|---|
| **sithulaka** | DNS 预筛漏斗 | 新增 `dns_prefilter.py` |
| **sithulaka** | 置信度评分系统 | 新增 CheckResult.confidence 字段 |
| **sithulaka** | Namecheap API ispremiumname | 新增 `providers/namecheap.py` |
| **saidutt46** | IANA Bootstrap 三层缓存 | 新增 `iana_bootstrap.py` |
| **saidutt46** | RDAP First + WHOIS Fallback | 重构 `check_domain()` 逻辑 |
| **saidutt46** | 流式结果（buffer_unordered） | 改成 async generator + SSE |
| **saidutt46** | TLD 预设系统 | 新增 presets 配置 |
| **saidutt46** | 语义化错误 | 新增 `indicates_available()` |
| **beast** | 指数退避重试 | 新增 `retry.py` |
| **beast** | 前端-后端解耦 | 保持 Vercel + Render 架构 |

### 5.2 Python 依赖建议

| 用途 | 依赖 | 理由 |
|---|---|---|
| RDAP 查询 | `httpx` | 异步，支持 HTTP/2 |
| WHOIS 查询 | `subprocess`（系统命令） | 跨平台，比 python-whois 灵活 |
| DNS 查询 | `dnspython` | 多记录类型支持 |
| 并发 | `asyncio` + `aiohttp` | 比 threading 适合 I/O |
| IANA 缓存 | `aiosqlite` | 持久化缓存 |

---

## 6. 关键源码摘录

### 6.1 IANA Bootstrap 核心（10 行 Python 实现）

```python
import requests
BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"

def fetch_iana_bootstrap():
    resp = requests.get(BOOTSTRAP_URL, timeout=10)
    data = resp.json()
    endpoints = {}
    for service in data["services"]:
        tlds = service[0]
        url = f"{service[1][0].rstrip('/')}/domain/"
        for tld in tlds:
            endpoints[tld.lower()] = url
    return endpoints
# 返回: {"com": "https://rdap.verisign.com/com/v1/domain/", "net": "...", ...}
```

### 6.2 WHOIS 服务器发现

```python
def discover_whois_server(tld):
    result = subprocess.run(
        ["whois", "-h", "whois.iana.org", tld],
        capture_output=True, text=True, timeout=10
    )
    for line in result.stdout.splitlines():
        if line.strip().startswith("refer:"):
            return line.split(":", 1)[1].strip()
    return None  # 回退到默认服务器
```

### 6.3 流式并发查询（asyncio.as_completed）

```python
async def check_domains_stream(domains, max_concurrency=20):
    semaphore = asyncio.Semaphore(max_concurrency)
    async def check_one(domain):
        async with semaphore:
            return await check_domain(domain)
    tasks = [check_one(d) for d in domains]
    for coro in asyncio.as_completed(tasks):
        yield await coro
```

### 6.4 Namecheap 溢价检测

```python
# Namecheap XML API 请求
# GET https://api.namecheap.com/xml.response?
#     ApiUser={user}&ApiKey={key}&UserName={user}&ClientIp={ip}
#     &Command=namecheap.domains.check&DomainList={domains}

# 响应解析
import xml.etree.ElementTree as ET
root = ET.fromstring(response_text)
for result in root.iter('DomainCheckResult'):
    domain = result.get('Domain')
    available = result.get('Available') == 'true'
    is_premium = result.get('IsPremiumName') == 'true'
```

---

## 附录：项目链接

- [sithulaka/DomainChecker](https://github.com/sithulaka/DomainChecker/blob/main/main.py)
- [saidutt46/domain-check](https://github.com/saidutt46/domain-check)
  - [IANA Bootstrap 实现](https://github.com/saidutt46/domain-check/blob/main/domain-check-lib/src/protocols/registry.rs)
  - [RDAP 客户端](https://github.com/saidutt46/domain-check/blob/main/domain-check-lib/src/protocols/rdap.rs)
  - [WHOIS 客户端](https://github.com/saidutt46/domain-check/blob/main/domain-check-lib/src/protocols/whois.rs)
  - [错误类型定义](https://github.com/saidutt46/domain-check/blob/main/domain-check-lib/src/error.rs)
  - [类型定义](https://github.com/saidutt46/domain-check/blob/main/domain-check-lib/src/types.rs)
- [l3lackcurtains/beast-domain-checker](https://github.com/l3lackcurtains/beast-domain-checker)
  - [核心自动化逻辑](https://github.com/l3lackcurtains/beast-domain-checker/blob/main/src/lib/domainChecker.ts)
  - [API 端点](https://github.com/l3lackcurtains/beast-domain-checker/blob/main/src/pages/api/check-domains.ts)