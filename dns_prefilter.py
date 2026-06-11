"""
DNS 预筛漏斗 — 在 RDAP/WHOIS 查询前先查 DNS

原理：
  80% 的已注册域名都有 DNS 记录（A/AAAA/MX/NS/CNAME/SOA）。
  用本地 DNS 查询只需 5-50ms，远快于 RDAP HTTP 请求（500-2000ms）。

效果：
  - 已注册域名：5ms 判定，不产生 HTTP 请求
  - 可注册域名：DNS 无记录 → 继续走 RDAP/WHOIS 确认
  - 总请求量减少 80%，限速概率大幅降低
"""
import socket


try:
    import dns.resolver
    HAS_DNSPYTHON = True
except ImportError:
    HAS_DNSPYTHON = False


def is_fake_or_private_ip(ip: str) -> bool:
    """快速判断是否是内网/局域网IP、回环IP或 Clash 默认假 IP 范围 (198.18.0.0/15)"""
    if not ip:
        return True
    
    # IPv4 检查
    if "." in ip:
        if ip.startswith("127."):
            return True
        if ip.startswith("198.18.") or ip.startswith("198.19."):
            return True
        if ip.startswith("10."):
            return True
        if ip.startswith("192.168."):
            return True
        if ip.startswith("169.254."):
            return True
        if ip.startswith("172."):
            try:
                parts = ip.split('.')
                if len(parts) >= 2:
                    second_octet = int(parts[1])
                    if 16 <= second_octet <= 31:
                        return True
            except ValueError:
                pass
        if ip == "0.0.0.0":
            return True
            
    # IPv6 检查
    elif ":" in ip:
        ip_lower = ip.lower()
        if ip_lower == "::1" or ip_lower == "0:0:0:0:0:0:0:1":
            return True
        # ULA 局域网独有地址 (fc00::/7)
        if ip_lower.startswith("fc") or ip_lower.startswith("fd"):
            return True
        # 链路本地地址 (fe80::/10)
        if ip_lower.startswith("fe8") or ip_lower.startswith("fe9") or ip_lower.startswith("fea") or ip_lower.startswith("feb"):
            return True
            
    return False


def has_dns_record(domain: str) -> bool:
    """快速判断域名是否有 DNS 记录
    如果安装了 dnspython，优先查询 SOA 记录，这可以过滤出几乎所有已注册域名（包括停放、无 A 记录的域名）。
    """
    if HAS_DNSPYTHON:
        try:
            resolver = dns.resolver.Resolver()
            resolver.timeout = 1.5
            resolver.lifetime = 1.5
            resolver.resolve(domain, 'SOA')
            return True
        except dns.resolver.NXDOMAIN:
            return False
        except (dns.resolver.NoNameservers, dns.resolver.NoAnswer):
            # Clash 拦截 SOA 可能导致 NoNameservers / NoAnswer。
            # 这里应 pass 降级让 A/AAAA 检查（能过滤 fake-ip）及后续 RDAP 验证，杜绝假阳性。
            pass
        except Exception:
            pass

    # 查 A 记录（IPv4）
    try:
        info = socket.getaddrinfo(domain, 0, socket.AF_INET, socket.SOCK_STREAM)
        has_real_ip = False
        for item in info:
            ip = item[4][0]
            if not is_fake_or_private_ip(ip):
                has_real_ip = True
                break
        if has_real_ip:
            return True
    except socket.gaierror:
        pass

    # 查 AAAA 记录（IPv6）
    try:
        info = socket.getaddrinfo(domain, 0, socket.AF_INET6, socket.SOCK_STREAM)
        has_real_ip = False
        for item in info:
            ip = item[4][0]
            if not is_fake_or_private_ip(ip):
                has_real_ip = True
                break
        if has_real_ip:
            return True
    except socket.gaierror:
        pass

    return False


def has_dns_detailed(domain: str) -> dict:
    """详细的 DNS 检查，返回每条记录的状态"""
    result = {
        "has_a": False,
        "has_aaaa": False,
        "has_mx": False,
        "has_ns": False,
        "a_records": [],
        "any": False,
    }

    # A 记录
    try:
        info = socket.getaddrinfo(domain, 0, socket.AF_INET, socket.SOCK_STREAM)
        ips = list(set(item[4][0] for item in info))
        real_ips = [ip for ip in ips if not is_fake_or_private_ip(ip)]
        if real_ips:
            result["has_a"] = True
            result["a_records"] = real_ips[:5]  # 最多保留 5 个
    except socket.gaierror:
        pass

    # AAAA 记录
    try:
        info = socket.getaddrinfo(domain, 0, socket.AF_INET6, socket.SOCK_STREAM)
        ips = list(set(item[4][0] for item in info))
        real_ips = [ip for ip in ips if not is_fake_or_private_ip(ip)]
        if real_ips:
            result["has_aaaa"] = True
    except socket.gaierror:
        pass

    result["any"] = result["has_a"] or result["has_aaaa"] or result["has_mx"] or result["has_ns"]
    return result
