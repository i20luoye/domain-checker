# 项目当前进度、存在问题与优化方向审计报告

本报告对当前“域名批量查询工具”项目的开发进度、本地与 Vercel 部署的代码差异、潜在的瓶颈和技术问题进行全面梳理，并提出下一步的优化演进路线。

---

## 一、 项目当前进度

当前项目已经完成了基础的“免费部署”和“本地运行”两个维度的架构搭建，主要进展如下：

| 模块 | 文件 | 状态 | 核心实现与功能 |
|---|---|---|---|
| **Vercel 前端** | [public/index.html](file:///f:/codex/域名查询/public/index.html) | ✅ 已完成 | 现代暗色玻璃拟态（Glassmorphism）UI，支持前缀/长度/后缀/字母范围的组合生成；支持**暂停/继续/停止**状态控制；支持实时数据过滤（状态、首字母、关键字）及带有 Excel BOM 的 CSV 导出；支持分批次请求（`batchSize = 10`）以应对接口超时限制。 |
| **Vercel 后端** | [api/check.py](file:///f:/codex/域名查询/api/check.py) | ✅ 已完成 | 纯 Python 标准库实现（无需 `requirements.txt` 依赖）。支持大部分后缀的 RDAP 并发查询与 CNNIC WHOIS 端口 43 代理；支持启发式域名溢价猜测（`get_premium_reason`），单次处理 20 个并发查询。 |
| **本地/VPS 后端** | [domain_server.py](file:///f:/codex/域名查询/domain_server.py) | ✅ 已完成 | 用于独立 VPS 部署的 Python HTTP 服务端，基于 `asyncio` 和 `aiohttp`。支持导入 `domain_checker` 实现的高级数据源与容灾判定。 |
| **本地/VPS 核心** | [domain_checker.py](file:///f:/codex/域名查询/domain_checker.py) | ✅ 已完成 | 原始 CLI 脚本的增强版（v6），内部实现了**DNS 预筛漏斗**、**多 Provider 抽象熔断器（Circuit Breaker）**，以及多种第三方 API 适配（Botoi、Porkbun、Domainr、WhoisFreaks 和 GoDaddy API）。 |
| **部署配置** | [vercel.json](file:///f:/codex/域名查询/vercel.json) | ✅ 已完成 | Vercel Serverless 执行配置（限制 10 秒超时，256MB 内存），加装无缓存响应头。 |

---

## 二、 现有问题与技术瓶颈

在对比本地版 `domain_checker.py` 与 Vercel Serverless 版 [api/check.py](file:///f:/codex/域名查询/api/check.py) 后，我们发现了以下几个关键的**设计割裂与性能瓶颈**：

### 1. Vercel 后端与本地版功能的严重割裂（API 逻辑分裂）
*   **问题表现**：本地版 `domain_checker.py` 拥有强大的新特性（如 DNS 快速预筛、多数据源熔断、GoDaddy 生产 API 批量查询等）。但 Vercel 后端 [api/check.py](file:///f:/codex/域名查询/api/check.py) 是完全独立手写的，**完全缺失了这些高级优化**。
*   **后果**：用户一旦将项目部署到 Vercel：
    *   **无法查询真正的溢价价格**：即使在 Vercel 配置了 `GODADDY_KEY` 环境变量，由于 [api/check.py](file:///f:/codex/域名查询/api/check.py) 里没有实现 GoDaddy 请求逻辑，系统也会完全忽略。
    *   **后缀支持受限**：Vercel 仅支持硬编码的 8 种常用后缀，新后缀（如 `.xyz`, `.top`）在 Vercel 部署版中无法查询。

### 2. Vercel 接口缺失 “DNS 预筛”，容易触发频控 (HTTP 429)
*   **问题表现**：在批量查询时，Vercel 版的 [api/check.py](file:///f:/codex/域名查询/api/check.py) 无论域名是否已被注册，都会直接向公共 RDAP 权威服务器发送 HTTP 请求。
*   **后果**：查询 10,000 个域名就产生 10,000 次真实 HTTP 请求，极易触发 Verisign 等注册局的 HTTP 429（限速）导致大面积“查询失败”。而本地版已支持 DNS 预筛（已注册域名先用 DNS 快速过滤，过滤率达 80% 以上）。

### 3. Vercel 10秒超时限制与并发死锁隐患
*   **问题表现**：Vercel 免费版的 Serverless 函数最长执行时间为 **10秒**（代码中配置了 `maxDuration: 10`）。[api/check.py](file:///f:/codex/域名查询/api/check.py) 内部使用的是同步的 `ThreadPoolExecutor`。
*   **后果**：如果前端一次发送 20 个域名，并且其中多个域名因网络问题出现响应缓慢，在 ThreadPool 中阻塞，很容易导致整个 Serverless 函数运行超过 10 秒而被 Vercel 强制中断（Aborted），导致前端接收不到任何数据。

### 4. 启发式溢价算法的误报与不确定性
*   **问题表现**：当前虽然通过词根、长度匹配等对“可能溢价”做了过滤，但这仅是“猜测”。对于真正想购买域名的用户，不能准确得知价格是一个痛点。

---

## 三、 优化方向与演进方案

为了将本项目打造成真正高稳定性、高性能、支持“溢价精准查价”的 state-of-the-art 工具，我们应该沿以下方向进行深度重构和优化：

### 1. 双模融合：重写 Vercel 后端 `api/check.py`
将 `domain_checker.py` 中的优秀设计（DNS 预筛、指数退避重试、多 Provider 适配）移植到 Vercel Serverless 后端，并使用**纯标准库**重写，确保无需第三方库，保持零依赖：
*   **移植 DNS 预筛漏斗**：在 [api/check.py](file:///f:/codex/域名查询/api/check.py) 的 `check_domain` 最前端加上 `socket.getaddrinfo` 校验。直接在 5ms 内拦截已解析的已注册域名，减轻 RDAP 负担。
*   **加入可选的 GoDaddy / NameSilo 批量 API 逻辑**：读取 Vercel 的环境变量。如果用户配置了 `GODADDY_KEY` 和 `GODADDY_SECRET`，则调用商业查询返回真实价格；若未配置，则回退到 RDAP+DNS 的免费查询模式。

### 2. 动态路由：引入 IANA Bootstrap 解析
*   在后端加入对 IANA 官方 `dns.json` 数据的轻量级下载和缓存逻辑（在 Vercel 中可以缓存到 `/tmp` 或直接内存缓存），使 Vercel 部署版能动态路由支持 1200+ 种后缀，摆脱硬编码字典的束缚。

### 3. 增强容错：指数退避重试机制 (Exponential Backoff)
*   不管是 RDAP 还是商业 API，当接口返回 `429`（限速）或 `503`（不可用）时，函数不应直接报错，而是应使用指数级增长的延迟（如 1s -> 2s -> 4s）自动重试，确保大任务下的最终成功率。

### 4. 前端 UI 与 API 协议的深度对齐
*   **自适应超限提示**：当前端检测到查询组合超过 10 万时，自动提醒用户可能面临限速，建议使用 DNS 预筛并调小并发。
*   **状态展示优化**：在前端表格中为经过 DNS 预筛“秒回”的已注册域名显示不同的来源标识（如“DNS预筛”），让用户直观感受到速度的提升。
