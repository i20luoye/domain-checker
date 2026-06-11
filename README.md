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
| `method` | 查询方法 | primary / consensus / whois / dns_prefilter |
| `confidence` | 置信度 | VERY_HIGH / HIGH / MEDIUM / LOW |

## 免费权威源链路

默认部署只使用免费公开协议：

| 链路 | 用途 | 准确性说明 |
|---|---|---|
| DNS 预筛 | 快速识别大量已注册域名 | 只能预筛，不能单独作为最终结论 |
| RDAP 主源 | `.com/.net/.org/.app/.dev/.ai` 等权威查询 | `404` 可视为高置信可注册，`200` 可视为已注册 |
| RDAP 多源复核 | 主源超时或 DNS 预筛冲突时使用 | 免费源多源投票，降低误判 |
| WHOIS 43 | `.cn/.com.cn` 等 RDAP 不稳定后缀 | 可用但网络上偶发 reset，失败时标“需复核” |

如果免费权威源都没有明确结论，结果会标为 `error`，前端显示为“需复核”，不会硬判为可注册或已注册。

`.cn` 默认启用严格模式：

- 已注册：任一次 CNNIC WHOIS 返回 `Domain Name:` 即判定为已注册。
- 可注册：必须连续两次返回 `No matching record.` 才判定为可注册。
- 空响应、连接重置、一次成功一次失败：全部标为“需复核”。

导出“可注册域名”时，前端会再次调用后端复核，只导出复核后仍然可注册的域名。

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
- **VERY_HIGH**：RDAP 404 确认可注册 / 免费多源复核可注册
- **HIGH**：RDAP 200 确认已注册 / DNS + RDAP 双重确认
- **MEDIUM**：WHOIS 端口 43 或单源结果
- **LOW**：免费源无明确结论，需要人工复核

## 关于溢价域名

免费 RDAP/WHOIS 只能判断域名是否已注册，不能返回真实注册价格。

所以本项目免费模式里的“可能溢价”是启发式提示，不是注册局或注册商报价。它会根据短域名、英文词根、重复字母、回文、顺序字母等规则提醒你重点复核。

如果你的目标是给客户大量免费查询，建议展示为：

```text
可注册 / 已注册 / 需复核 / 可能溢价（需人工复核）
```

不要承诺“真实价格”或“准确溢价价格”。
