# 域名批量查询工具 v7 使用说明

## 当前推荐：免费 API 模式

本项目默认只使用免费公开协议：

- `.com/.net/.org/.ai/.app/.dev/.io`：RDAP
- `.cn`：CNNIC WHOIS 43 端口

这种方式不需要 GoDaddy、Namecheap、Dynadot 等商业 API Key，也没有商业 API 额度和扣费问题。

## 文件清单

- `domain_server.py`：可部署 Web 服务端，推荐部署用这个
- `domain_checker.html`：浏览器页面，通过服务端 `/api/check` 查询
- `domain_checker.py`：命令行批量查询脚本
- `run_free_server.bat`：Windows 本地一键启动免费服务端
- `部署到服务器.md`：Linux/Nginx 部署说明
- `run_godaddy.bat`：旧的 GoDaddy 沙箱脚本，不推荐使用

## 本地启动 Web 版

```bash
python domain_server.py --host 127.0.0.1 --port 8000
```

然后打开：

```text
http://127.0.0.1:8000/
```

Windows 也可以双击：

```text
run_free_server.bat
```

## 命令行查询

### 查 ai + 3 字母 + .com

```bash
python domain_checker.py --source rdap --prefix ai --length 5 --suffix com
```

### 查 ai + 4 字母 + .com/.cn

```bash
python domain_checker.py --source rdap --prefix ai --length 6 --suffix com cn
```

### 查 am 结尾 + 5 字母 + .com

```bash
python domain_checker.py --source rdap --prefix "" --length 5 --custom-suffix am --suffix com
```

### 拆批查 5 字母 ai + .com

```bash
python domain_checker.py --source rdap --prefix ai --length 7 --suffix com --letters a-m --output part1.csv
python domain_checker.py --source rdap --prefix ai --length 7 --suffix com --letters n-z --output part2.csv
```

## CSV 字段

| 字段 | 含义 | 示例 |
|---|---|---|
| `domain` | 完整域名 | `aigames.com` |
| `status` | available / taken / error | available |
| `premium` | 是否命中启发式溢价规则 | True / False |
| `premiumReason` | 启发式原因 | 短域名 / 词根(box) |
| `method` | 查询方法 | primary / consensus / whois / godaddypublic / rdap |
| `price_usd` | GoDaddy 真实报价（仅 godaddy/godaddypublic 源能返回） | 12.99 |

## 多 Provider 故障转移链路

当某个数据源失败（限流、网络错误）时，自动切到下一个。支持 6 个数据源：

| Provider | 限速 | 真实价格 | 溢价检测 | 备注 |
|---|---|---|---|---|
| `rdap` | 视各 TLD 服务器 | ❌ | 启发式 | 默认首选，零配置 |
| `godaddypublic` | 30 req/min | ❌ | 真实信号 ⭐ | 无需 Key，公共 MCP 端点 |
| `porkbun` | - | ✅ | 真实 | 需 `PORKBUN_KEY`/`PORKBUN_SECRET` |
| `domainr` | 10k/月 (RapidAPI) | 部分 | 部分 | 需 `DOMAINR_RAPIDAPI_KEY` |
| `whoisfreaks` | 500 免费 credits | ✅ | 真实 | 需 `WHOISFREAKS_KEY` |
| `botoi` | 5 req/min, 100 req/day | ❌ | 不可靠 ⚠️ | 免 Key，后端 RDAP 经常盲报 |

### 用法

```bash
# 默认链路（按优先级依次尝试）
python domain_checker.py --source chain --prefix ai --length 7 --suffix com

# 自定义链路
python domain_checker.py --source chain --providers rdap,godaddypublic,porkbun \
    --prefix ai --length 7 --suffix com
```

熔断规则：连续 5 次错误 → 30 秒冷却；限流 → 60 秒冷却；冷却期间自动跳过该 provider。

## TLD 预设 + 置信度评分

```bash
# 一键选择常用 TLD 组合
python domain_checker.py --prefix ai --length 5 --tld-preset startup
python domain_checker.py --prefix ai --length 5 --tld-preset tech
python domain_checker.py --prefix ai --length 5 --tld-preset finance
python domain_checker.py --prefix ai --length 5 --tld-preset cn

# 附加 confidence 字段（VERY_HIGH / HIGH / MEDIUM / LOW）到 CSV
python domain_checker.py --prefix ai --length 5 --suffix com --with-confidence
```

| 预设 | TLD 列表 |
|---|---|
| `startup` | com, org, io, ai, tech, app, dev, xyz |
| `tech` | io, ai, app, dev, tech, cloud, software |
| `creative` | design, art, studio, media, photo |
| `finance` | finance, capital, fund, money, bank |
| `cn` | com, cn, com.cn, net.cn |
| `all` | IANA 全部 1200+ TLD |

置信度等级：
- **VERY_HIGH**：RDAP 404 确认可注册 / DNS 有记录确认已注册
- **HIGH**：RDAP 200（已注册）/ 商业 API 返回
- **MEDIUM**：仅 DNS 预筛 / WHOIS 端口 43 / 多源失败但 DNS 无记录
- **LOW**：所有数据源都失败

## 关于溢价域名

免费 RDAP/WHOIS 只能判断域名是否已注册，不能返回真实注册价格。

所以本项目免费模式里的“可能溢价”是启发式提示，不是注册局或注册商报价。它会根据短域名、英文词根、重复字母、回文、顺序字母等规则提醒你重点复核。

如果你的目标是给客户大量免费查询，建议展示为：

```text
可注册 / 已注册 / 查询失败 / 可能溢价（需人工复核）
```

不要承诺“真实价格”或“准确溢价价格”。
