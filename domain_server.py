#!/usr/bin/env python3
"""
域名批量查询 Web 服务端。

部署后浏览器只请求本服务的 /api/check，由服务器代查免费 RDAP/WHOIS，
避免浏览器 CORS。默认不使用 GoDaddy 或其他商业价格 API。
"""
import argparse
import asyncio
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import aiohttp

import domain_checker


ROOT = Path(__file__).resolve().parent
MAX_ITEMS_PER_REQUEST = 500
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
    semaphore = asyncio.Semaphore(workers)
    connector = aiohttp.TCPConnector(limit=max(workers * 2, 10), ssl=False)
    headers = {
        "User-Agent": "Mozilla/5.0 DomainCheckerServer/1.0",
        "Accept": "application/json",
    }
    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        tasks = [
            domain_checker.check_domain(session, item["domain"], item["suffix"], semaphore)
            for item in items
        ]
        return await asyncio.gather(*tasks)


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
                "supportedSuffixes": sorted(domain_checker.RDAP_SOURCES.keys()),
            })
            return

        rel = "domain_checker.html" if parsed.path == "/" else unquote(parsed.path.lstrip("/"))
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
