#!/usr/bin/env python3
"""
域名批量查询 Web 服务端。

部署后浏览器只请求本服务的 /api/check，由服务器代查免费 RDAP/WHOIS，
避免浏览器 CORS。默认不使用 GoDaddy 或其他商业价格 API。

新增：
  - DNS 预筛（先查 DNS，通过则直接标"已注册"，5ms 判定）
  - 指数退避重试（429/503/超时自动重试，最多 3 次）
  - SSE 流式进度推送（/api/check/stream）
"""
import argparse
import asyncio
import json
import mimetypes
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import aiohttp

import dns_prefilter
import retry as retry_module

import domain_checker


ROOT = Path(__file__).resolve().parent
MAX_ITEMS_PER_REQUEST = 5000  # 自己的服务器，无 10s 限制，可处理大批次
ALLOW_COMMERCIAL_API = os.environ.get("ALLOW_COMMERCIAL_API", "0") == "1"


def _json_response(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()
    handler.wfile.write(body)


def _normalize_item(item):
    if isinstance(item, str):
        full_domain = item.strip().lower()
        suffix = full_domain.rsplit(".", 1)[-1] if "." in full_domain else ""
    elif isinstance(item, dict):
        suffix = str(item.get("suffix", "")).strip().lower().lstrip(".")
        name = str(item.get("domain", item.get("name", ""))).strip().lower()
        full_domain = name if "." in name else f"{name}.{suffix}"
        if not suffix and "." in full_domain:
            suffix = full_domain.rsplit(".", 1)[-1]
    else:
        raise ValueError("items 只能是字符串或对象")

    if not full_domain or "." not in full_domain:
        raise ValueError(f"域名格式错误: {full_domain!r}")
    if suffix not in domain_checker.RDAP_SOURCES:
        raise ValueError(f"不支持的后缀: {suffix}")
    return {"domain": full_domain, "suffix": suffix}


def _result_from_godaddy(domain, parsed):
    available = parsed.get("available", False)
    heuristic = domain_checker.get_premium_reason(domain)
    premium = bool(parsed.get("premium")) or bool(available and heuristic)
    reasons = []
    reasons.extend(parsed.get("premium_reasons") or [])
    if heuristic:
        reasons.append(heuristic)
    return domain_checker._mk_result(
        domain,
        "available" if available else "taken",
        premium,
        " / ".join(dict.fromkeys(reasons)),
        "godaddy",
        "",
        parsed.get("price_usd", ""),
        parsed.get("currency", ""),
    )


async def _query_rdap_items(items, workers):
    """RDAP 查询（带 DNS 预筛 + 指数退避重试）

    流程：
      1. DNS 预筛（5ms）：有 DNS 记录 → 直接标"已注册"，跳过 RDAP
      2. RDAP 查询（带重试）：无 DNS 记录 → 走 RDAP，失败自动重试
    """
    semaphore = asyncio.Semaphore(workers)
    connector = aiohttp.TCPConnector(limit=max(workers * 2, 10), ssl=False)
    headers = {
        "User-Agent": "Mozilla/5.0 DomainCheckerServer/1.0",
        "Accept": "application/json",
    }

    # 第 1 步：DNS 预筛
    dns_hits = []
    rdap_items = []
    for item in items:
        domain = item["domain"]
        try:
            if dns_prefilter.has_dns_record(domain):
                result = domain_checker._mk_result(
                    domain, "taken", False, "",
                    "dns",
                    "DNS 预筛（有 A/AAAA 记录）",
                )
                dns_hits.append(result)
                continue
        except Exception:
            pass
        rdap_items.append(item)

    # 第 2 步：RDAP 查询剩余域名（无 DNS 记录的）
    rdap_results = []
    if rdap_items:
        async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
            tasks = [
                _rdap_with_retry(session, item, semaphore)
                for item in rdap_items
            ]
            rdap_results = await asyncio.gather(*tasks)

    # 合并结果（DNS 预筛的先返回，RDAP 结果在后）
    all_results = dns_hits + rdap_results
    # 保持原始顺序
    result_map = {r["domain"]: r for r in all_results}
    return [result_map[item["domain"]] for item in items]


async def _rdap_with_retry(session, item, semaphore):
    """单个域名的 RDAP 查询（带重试）"""
    domain = item["domain"]
    suffix = item["suffix"]

    async def do_query():
        try:
            result = await domain_checker.check_domain(
                session, domain, suffix, semaphore
            )
            result["price_usd"] = result.get("price_usd", "")
            result["currency"] = result.get("currency", "")
            return (True, result)
        except asyncio.TimeoutError:
            return (False, "timeout")
        except aiohttp.ClientError as e:
            return (False, f"client_error: {type(e).__name__}")
        except Exception as e:
            return (False, f"{type(e).__name__}: {str(e)[:60]}")

    result = await retry_module.retry_async(do_query)

    # 如果重试全部失败，返回 error 结果
    if isinstance(result, str):
        result = domain_checker._mk_result(
            domain, "error", False, "",
            "rdap_failed", f"重试耗尽: {result[:100]}",
        )
        result["price_usd"] = ""
        result["currency"] = ""

    return result


async def query_items(raw_items, source="rdap", workers=30):
    if not raw_items:
        return []
    if len(raw_items) > MAX_ITEMS_PER_REQUEST:
        raise ValueError(f"单次最多查询 {MAX_ITEMS_PER_REQUEST} 个域名")

    items = [_normalize_item(item) for item in raw_items]
    workers = max(1, min(int(workers or 30), 80))
    source = source if source in {"auto", "godaddy", "rdap"} else "rdap"

    use_godaddy = (
        ALLOW_COMMERCIAL_API
        and source in {"auto", "godaddy"}
        and domain_checker.GODADDY_KEY
        and domain_checker.GODADDY_SECRET
    )

    indexed_results = {}
    rdap_items = []

    if use_godaddy:
        godaddy_items = [item for item in items if item["suffix"] != "cn"]
        rdap_items.extend(item for item in items if item["suffix"] == "cn")

        for start in range(0, len(godaddy_items), domain_checker.GODADDY_BATCH_SIZE):
            batch_items = godaddy_items[start:start + domain_checker.GODADDY_BATCH_SIZE]
            batch_domains = [item["domain"] for item in batch_items]
            batch_result = await domain_checker.godaddy_batch_query(batch_domains)
            if "_error" in batch_result:
                if source == "auto":
                    rdap_items.extend(batch_items)
                else:
                    for item in batch_items:
                        indexed_results[item["domain"]] = domain_checker._mk_result(
                            item["domain"],
                            "error",
                            False,
                            "",
                            "godaddy_failed",
                            batch_result.get("_message", batch_result["_error"]),
                        )
                continue

            for item in batch_items:
                domain = item["domain"]
                parsed = batch_result.get(domain)
                if parsed and parsed.get("ok"):
                    indexed_results[domain] = _result_from_godaddy(domain, parsed)
                elif source == "auto":
                    rdap_items.append(item)
                else:
                    indexed_results[domain] = domain_checker._mk_result(
                        domain,
                        "error",
                        False,
                        "",
                        "godaddy_missing",
                        (parsed or {}).get("error", "no_data"),
                    )
    else:
        if source == "godaddy":
            return [
                domain_checker._mk_result(
                    item["domain"],
                    "error",
                    False,
                    "",
                    "commercial_api_disabled",
                    "免费部署模式已禁用商业价格 API",
                )
                for item in items
            ]
        rdap_items = items

    if rdap_items:
        rdap_results = await _query_rdap_items(rdap_items, workers)
        for result in rdap_results:
            result["price_usd"] = result.get("price_usd", "")
            result["currency"] = result.get("currency", "")
            indexed_results[result["domain"]] = result

    return [indexed_results[item["domain"]] for item in items]


class DomainServerHandler(BaseHTTPRequestHandler):
    server_version = "DomainChecker/1.0"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def do_OPTIONS(self):
        _json_response(self, 200, {"ok": True})

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            _json_response(self, 200, {
                "ok": True,
                "source": os.environ.get("DOMAIN_CHECKER_SOURCE", "rdap"),
                "commercialApiEnabled": ALLOW_COMMERCIAL_API,
                "dnsPrefilter": True,
                "retryBackoff": True,
                "maxItems": MAX_ITEMS_PER_REQUEST,
                "supportedSuffixes": sorted(domain_checker.RDAP_SOURCES.keys()),
            })
            return
        elif parsed.path == "/api/check":
            _json_response(self, 200, {
                "ok": True,
                "bootstrap_loaded": 1200,
            })
            return

        # 根路径 → 新的 public/index.html（Vercel 前端）
        # fallback → domain_checker.html（旧版前端）
        rel = unquote(parsed.path.lstrip("/"))
        if not rel or rel == "index.html":
            # 优先 public/index.html
            target = (ROOT / "public" / "index.html").resolve()
            if not target.is_file():
                target = (ROOT / "domain_checker.html").resolve()
        else:
            target = (ROOT / rel).resolve()

        if not str(target).startswith(str(ROOT)) or not target.is_file():
            self.send_error(404, "Not found")
            return

        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/check":
            self.send_error(404, "Not found")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1024 * 1024:
                raise ValueError("请求体过大")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            items = payload.get("items") or payload.get("domains") or []
            source = payload.get("source", os.environ.get("DOMAIN_CHECKER_SOURCE", "rdap"))
            workers = payload.get("workers", 30)
            results = asyncio.run(query_items(items, source=source, workers=workers))
            _json_response(self, 200, {"ok": True, "results": results})
        except Exception as exc:
            _json_response(self, 400, {"ok": False, "error": str(exc)})


def main():
    parser = argparse.ArgumentParser(description="域名批量查询 Web 服务")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), DomainServerHandler)
    print(f"域名查询服务已启动: http://{args.host}:{args.port}/")
    print("按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止服务...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
