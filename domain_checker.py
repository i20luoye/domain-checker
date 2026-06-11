#!/usr/bin/env python3
"""
域名批量查询工具 - 多源 RDAP + 通用配置版
支持任意前缀 + 任意长度 + 任意后缀 + 任意字母范围
使用 RDAP 协议，无需 API Key

用法示例：
  # 查 ai+3位+空+com/cn（原始用法）
  python domain_checker.py

  # 查 ai+6位+空+com
  python domain_checker.py --length 8 --suffix com

  # 查空+6位+am+com/cn
  python domain_checker.py --prefix "" --length 8 --custom-suffix am --suffix com cn

  # 拆分大任务：先 a~m
  python domain_checker.py --length 8 --letters a-m

  # 提高并发
  python domain_checker.py --workers 50
"""
import asyncio
import itertools
import string
import csv
import time
import threading
import argparse
import os
import socket
import sys
from datetime import datetime

# ================== GoDaddy 批量 API 配置 ==================
# 优先从环境变量读取，避免硬编码泄露
GODADDY_KEY = os.environ.get("GODADDY_KEY", "")
GODADDY_SECRET = os.environ.get("GODADDY_SECRET", "")
# 沙箱 OTE：https://api.ote-godaddy.com  (不扣费)
# 生产 PROD：https://api.godaddy.com
GODADDY_BASE = os.environ.get("GODADDY_BASE", "https://api.ote-godaddy.com")
# GoDaddy 批量接口单次最多 ~500 个域名
GODADDY_BATCH_SIZE = 500

# Windows 终端中文输出修复
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

try:
    import aiohttp
except ImportError:
    print("正在安装依赖 aiohttp ...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "aiohttp"])
    import aiohttp

# ================== 多 Provider 链路配置 ==================
# 第三方免费 / 免费 Key 数据源（按需启用）
# Botoi：免 Key，5 req/min，100 req/day（轻量补位用）
BOTOI_ENDPOINT = "https://api.botoi.com/v1/domain/availability"
# Porkbun：需 Key（PORKBUN_KEY + PORKBUN_SECRET），免费注册就送，real price
PORKBUN_KEY = os.environ.get("PORKBUN_KEY", "")
PORKBUN_SECRET = os.environ.get("PORKBUN_SECRET", "")
PORKBUN_ENDPOINT = "https://api.porkbun.com/api/json/v3/domain/checkDomain"
# Domainr：需 RapidAPI Key（X-RapidAPI-Key），10k requests/month free
DOMAINR_RAPIDAPI_KEY = os.environ.get("DOMAINR_RAPIDAPI_KEY", "")
DOMAINR_ENDPOINT = "https://domainr.p.rapidapi.com/v2/status"
# WhoisFreaks：需 Key，500 free credits（一次性，适合小批量）
WHOISFREAKS_KEY = os.environ.get("WHOISFREAKS_KEY", "")
WHOISFREAKS_ENDPOINT = "https://api.whoisfreaks.com/v1.0/domain/availability"

# Provider 链路熔断配置
PROVIDER_BREAKER_ERROR_THRESHOLD = 5      # 连续错误数 → 触发熔断
PROVIDER_BREAKER_RATE_LIMIT_COOLDOWN = 60 # 限流后冷却秒数
PROVIDER_BREAKER_ERROR_COOLDOWN = 30      # 错误熔断后冷却秒数

# ================== RDAP 源（多源并发投票） ==================
RDAP_SOURCES = {
    "com": [
        "https://rdap.verisign.com/com/v1/domain/",
        "https://rdap.iana.org/domain/",
        "https://rdap.gname.com/domain/",
    ],
    "net": [
        "https://rdap.verisign.com/net/v1/domain/",
        "https://rdap.iana.org/domain/",
    ],
    "org": [
        "https://rdap.publicinterestregistry.org/rdap/domain/",
        "https://rdap.iana.org/domain/",
    ],
    # 关键：.cn 的 RDAP 在 IANA bootstrap 里不存在，CNNIC 自有 RDAP 也不可用（SSL 失败、用了文档示例 IP）
    # 用 CNNIC 的 whois 端口 43（标准协议），可用且稳定
    "cn": ["whois://whois.cnnic.cn:43"],
    # .io 三个源：identitydigital（推荐主源）+ nic.io（备用）+ iana.org（兜底）
    "io": [
        "https://rdap.identitydigital.services/rdap/domain/",
        "https://rdap.nic.io/domain/",
        "https://rdap.iana.org/domain/",
    ],
    "ai": [
        "https://rdap.nic.ai/domain/",
        "https://rdap.iana.org/domain/",
    ],
    "app": [
        "https://rdap.nic.google/domain/",
        "https://rdap.iana.org/domain/",
    ],
    "dev": [
        "https://rdap.nic.google/domain/",
        "https://rdap.iana.org/domain/",
    ],
}

# 常见 2~3 字母英文单词，用于溢价检测
COMMON_WORDS = set("""box car dog cat man boy kid sun sky sea map app web net biz
pro top vip fun run win lab job pay buy fit joy key law log
mix tax tea way zoo art bar bed big bit bus can cap cut day
die dry eat egg eye fan far fat fee fly gas get god gun guy
hit hot ice let lie lot low mad mom new nor now off oil old
one out own pen pie pop put red rid row sad say set sex she
sit six son ten too try use van war wet who why yes yet you
ace act add age ago aid aim air all any arm ask bad bag ban
bat bay beg bet bid bio bug cab cad cam cop cow cry cue cup
hub ink inn jam jar jaw jet job joy key kid lab lad lap led
log lot mad map max mix mob mom mud nap net new nod now nut
oak oil old one out owl pad pan pat pay pen pet pie pig pin
pop pot pug pup put ran rat raw red rib rid rip rob rod rot
row rug run sad sat say see set sew she shy sin sip sir sit
six sky sly son sun tap tar tea ten tie tip toe top toy try
tub tug two use van vet vow war wax web wet win wit wok woo
wow yap yea yes yet you zip zoo
hop run win fit let act add ask bet big buy cut die eat end fly get god got gun has hat hit hot how ink jam key kid lab let lie log lot mad map mat may mix net new now odd off old one out own pad pay pie pit put ran rat raw red rid row run sat say sea see set sick sir sit six sky son sun tap tar tea ten the tie tip toe too top try tub two use van vet war was wax wet who why win wit wow yes yet you zip zoo
bird baby shop card code data face file game home link mail news note play rate star tech test type view vote book chat city club cool deal disk dock door edge feed film fire fish food foot ford gift girl goal gold good grow hair hand hard hate head help here hero high hill hold hole host idea iron jack john join jump keep king know lack land late lazy lead life like line list live load lock long look lord lose love luck made make male mall mark meal meat meet mind miss mode moon more most move much must name navy near neat neck need nice nine none norm nose noun okay once only open over pace pack page paid pain pair palm park part pass past path peak pick pine pink pipe plan plot plug plus poem poet poll pond pool poor port post pour pray pull pump pure push race rain rank rare read real rear rely rent rest rich ride ring rise risk road rock rode role roll roof room root rope rose ruin rule rush safe said sake sale salt same sand sang save seal seat seed seek seem seen self sell send sept ship shut side sign site size skin slip slow snow soft soil sold sole some song soon sort soul spot stay stem step stop such suit sure swim tail take tale talk tall tank tape task taxi team tell tend tent term text than that them then they thin this thus tide till time tiny tire told toll tone took tool tops tore torn tour town trap tree trim trio trip true tube tuck tune turn twin type ugly undo unit upon used user vale vary vast verb very vest vice visa wade wage wait wake walk wall wand want ward warm warn wash wave ways weak wear week well went were west what when whom wide wife wild will wind wine wing wire wise wish with wood word wore work worm worn wrap yard year yell zero zone
travel stream studio stable static status stereo store strong struck studio submit switch symbol system tables target temple thanks thirty though ticket toward travel triple trying tunnel twelve twenty typing unique united unless unlike unsafe update useful valley victor vision volume wealth weapon weekly weight window winner winter within wonder wooden worker worthy wright yellow
abroad accept accuse across action active actual agreed almost amount annual answer anyhow appear around attack august baker battle beauty became become before behalf behind belong beside better beyond bishop border bother branch breath bridge bright broken budget burden bureau button camera cancer carbon career castle caught centre chance change charge chosen church circle closed closer coffee colony colour column combat coming common comply copper corner costly cotton county couple course covers create credit crisis custom damage danger dealer debate decade defeat defend define degree demand depend deputy desert design desire detail device differ dinner direct divide doctor dollar domain double driven driver during easily eating edited editor effect effort eighth either empire enable ending energy engage engine enjoy enough ensure entire entity equals escape estate ethnic evolve except expand expect expert export extend extent fabric facing factor failed fairly fallen family farmer father favour fellow female figure filing filter finger flight flower flying follow forced forest forget formal former foster fought fourth freely friend frozen future gained garden gather gender gentle giving global golden govern ground growth guilty handle happen hardly having health height helped hidden highly holder hoping humble impact import impose income indeed indoor inform injury inland insert inside intent invest island itself jersey joined junior keeper launch lawyer layout leader league leaves legacy length lesson lifted likely linear linked liquid listen little living locate longer looked losing lovely luxury making manage manner manual margin marked master matter medium member memory mental merely merger method middle mighty mining minute mirror mobile modern modest moment mother motion moving murder museum mutual namely narrow nature nearby nearly nobody normal notice notion number object obtain occupy offend office online option orange origin others outfit output oxford packed palace parent partly patent patrol patron paying pencil people period permit person phrase picked pillar placed planet player please plenty pocket poetry poison police policy prefer pretty prince prison profit proper proven public pursue raised random ranger rating reason recall recent record reduce reform region reject relate relief remain remote remove rental repair repeat report resign resist resort result retain retire return reveal review reward riding robust ruling runner sacred safety sample saving saying scheme school screen search season second secret sector secure seeing select seller senior series server settle severe sexual shadow shared shelter shifted should signal signed silent silver simple simply singer single sister slight slowly smooth social socket solely source spirit spoken spread spring square stable status steady stolen stored strand stream street stress strict strike string stroke strong struck studio submit sudden suffer summer summit supply surely survey switch symbol system taking talent target temple tender terror thanks thirty though ticket timber timing toward travel triple trying tunnel twelve twenty typing unique united unless unlike unsafe update useful valley victim vision volume wealth weapon weekly weight window winner winter within wonder wooden worker worthy wright yellow
app api ai ml llm gpt bot dev web net app biz pro vip lab hub app aiart aibot aicpa aicrm aidev aiedu aigame ailaw ailife aimed aimapainenews aipay airag aiseo aishow aisoft aistore aitech aivideo""".split())

# 顺序字母检测
def is_seq_forward(s):
    return all(ord(s[i]) == ord(s[i-1]) + 1 for i in range(1, len(s)))

def is_seq_backward(s):
    return all(ord(s[i]) == ord(s[i-1]) - 1 for i in range(1, len(s)))

VOWELS = set("aeiou")

def get_premium_reason(domain: str) -> str:
    """启发式检测溢价域名，返回原因字符串或 None"""
    base = domain.rsplit(".", 1)[0].lower()
    if len(base) < 2:
        return None

    # 1~3 字母：稀缺资源，几乎都是溢价
    if len(base) <= 3:
        if base in COMMON_WORDS:
            return "短单词"
        if len(set(base)) == 1:
            return "全相同字母"
        if base == base[::-1]:
            return "回文短域名"
        return "短域名"

    # 4 字母：稀缺+模式
    if len(base) == 4:
        if base in COMMON_WORDS:
            return "英文单词"
        if len(set(base)) == 1:
            return "全相同字母"
        if base == base[::-1]:
            return "回文"
        if base[:2] == base[2:]:
            return "abab重复"
        if base[0] == base[1] and base[2] == base[3]:
            return "aabb模式"
        if is_seq_forward(base):
            return "顺序字母"
        if is_seq_backward(base):
            return "倒序字母"
        if all(c in VOWELS for c in base):
            return "全元音"
        # 全辅音在 4 字符中通常只是随机键盘组合（如 qwer、qzqx），不应自动标溢价
        return None

    # 5 字母：判断是否包含词根（aibox = ai+box，aidog = ai+dog）
    if len(base) == 5:
        if base in COMMON_WORDS:
            return "英文单词"
        # 切掉前 1~2 字母看是否是单词
        for cut in [1, 2]:
            if base[cut:] in COMMON_WORDS:
                return f"词根({base[cut:]})"
            if (base[:cut] + base[cut+1:]) in COMMON_WORDS:
                return "词根变体"
        # 末尾/开头 3 字母是常见单词
        if base[2:] in COMMON_WORDS:
            return f"词尾词({base[2:]})"
        if base[:3] in COMMON_WORDS:
            return f"词头词({base[:3]})"
        if len(set(base)) == 1:
            return "全相同字母"
        if base == base[::-1]:
            return "回文"
        return None

    # 6 字母：可包含词根（aishop = ai+shop，aigames = ai+games）
    if len(base) == 6:
        if base in COMMON_WORDS:
            return "英文单词"
        if base[:3] == base[3:]:
            return "abcabc重复"
        if base == base[::-1]:
            return "回文"
        # 末尾 3 字母是常见单词（高频判断）
        if base[3:] in COMMON_WORDS:
            return f"词尾词({base[3:]})"
        if base[:3] in COMMON_WORDS:
            return f"词头词({base[:3]})"
        # 中间 3-4 字母是常见单词
        for start in range(1, 4):
            if base[start:start+3] in COMMON_WORDS:
                return f"含词({base[start:start+3]})"
        if len(set(base)) == 1:
            return "全相同字母"
        if all(c in VOWELS for c in base):
            return "全元音"
        if all(c not in VOWELS and c != "y" for c in base):
            return "全辅音"
        return None

    # 7 字母
    if len(base) == 7:
        if base in COMMON_WORDS:
            return "英文单词"
        if base[4:] in COMMON_WORDS:
            return f"词尾词({base[4:]})"
        if base[:3] in COMMON_WORDS:
            return f"词头词({base[:3]})"
        if len(set(base)) == 1:
            return "全相同字母"
        return None

    # 8 字母
    if len(base) == 8:
        if base in COMMON_WORDS:
            return "英文单词"
        if base[5:] in COMMON_WORDS:
            return f"词尾词({base[5:]})"
        if base[:3] in COMMON_WORDS:
            return f"词头词({base[:3]})"
        return None

    return None


def is_likely_premium(domain: str) -> bool:
    return get_premium_reason(domain) is not None


async def query_one_source(session, url, semaphore, timeout_sec=8):
    """单源查询带超时。URL 可以是 http(s)://...（RDAP）或 whois://host:port/domain（whois 协议）。"""
    async with semaphore:
        # whois:// 协议：直接走 TCP 端口 43
        if url.startswith("whois://"):
            # 格式: whois://host:port/domain
            rest = url[8:]  # 去掉 whois://
            # 拆分 host:port 和 domain
            if "/" in rest:
                host_port, domain = rest.split("/", 1)
            else:
                host_port, domain = rest, ""
            if ":" in host_port:
                host, port_str = host_port.rsplit(":", 1)
                port = int(port_str)
            else:
                host, port = host_port, 43
            return await whois_query(host, port, domain, timeout_sec)
        # RDAP HTTP 查询
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout_sec), ssl=False) as resp:
                return {"ok": True, "status": resp.status}
        except asyncio.TimeoutError:
            return {"ok": False, "error": "timeout"}
        except aiohttp.ClientError as e:
            return {"ok": False, "error": f"client_err: {type(e).__name__}"}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:50]}"}


def whois_query_sync(host, port, domain, timeout_sec=8):
    """同步 whois 查询，返回 dict 模拟 RDAP 响应（taken/available/error）

    CNNIC 响应规则（实测验证）：
    - 已注册：返回包含 "Domain Name:" 字段
    - 未注册：返回包含 "No matching"（实际是 "No matching record." 中文 "未找到"）
    - 极少见：空响应（视为可注册，需用户人工复核）
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout_sec)
        s.connect((host, port))
        s.send((domain + "\r\n").encode())
        data = b''
        while True:
            try:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
            except socket.timeout:
                break
        s.close()
        text = data.decode('utf-8', errors='ignore').strip()

        if not text:
            # 空响应：CNNIC 偶尔返回空，按可注册处理
            return {"ok": True, "status": 404, "warning": "empty_response"}

        text_lower = text.lower()
        # CNNIC 关键词：未匹配 / 未找到 / not found
        if ('no matching' in text_lower
            or 'not found' in text_lower
            or 'no entries' in text_lower
            or '未找到' in text
            or '无匹配' in text):
            return {"ok": True, "status": 404}

        # 有 Domain Name 字段或注册人信息 = 已注册
        if 'domain name:' in text_lower or 'registrant:' in text_lower or 'status:' in text_lower:
            return {"ok": True, "status": 200}

        # 兜底：无法识别，标 unknown
        return {"ok": True, "status": 200, "warning": "unparsed"}
    except socket.timeout:
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:50]}"}


async def whois_query(host, port, domain, timeout_sec=8):
    """异步包装 whois_query_sync（asyncio 不能直接用同步 socket）"""
    return await asyncio.to_thread(whois_query_sync, host, port, domain, timeout_sec)


# ================== DNS 预筛漏斗（借鉴 sithulaka/DomainChecker） ==================
# 原理：已注册域名必有 DNS 记录。socket.getaddrinfo() 在 5ms 内可判定。
# 效果：80% 已注册域名被本地拦截，RDAP/WHOIS 请求量降低 80%
DNS_PREFILTER_TIMEOUT = 1.5  # 单次 DNS 查询超时（秒）
# 模块级开关（可通过 CLI --no-dns-prefilter 关闭）
DNS_PREFILTER_ENABLED = True


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


_dns_hijacked = None


def is_dns_hijacked() -> bool:
    """动态探测本地 DNS 是否存在通配符劫持 (ISP 劫持或 Clash 假 IP 且无法用 is_fake_or_private_ip 彻底过滤)"""
    global _dns_hijacked
    if _dns_hijacked is not None:
        return _dns_hijacked
        
    import uuid
    # 随机生成一个绝对不存在的域名
    random_domain = f"detect-nxdomain-{uuid.uuid4().hex[:12]}.com"
    try:
        info = socket.getaddrinfo(random_domain, None, socket.AF_INET, socket.SOCK_STREAM)
        has_real_ip = False
        for item in info:
            ip = item[4][0]
            if not is_fake_or_private_ip(ip):
                has_real_ip = True
                break
        _dns_hijacked = has_real_ip
    except Exception:
        _dns_hijacked = False
        
    return _dns_hijacked


def _dns_check_one(domain: str) -> bool:
    """检查域名是否有 A 记录。返回 True = 有 DNS 记录 → 大概率已注册"""
    if is_dns_hijacked():
        return False
    try:
        info = socket.getaddrinfo(domain, None, socket.AF_INET, socket.SOCK_STREAM)
        for item in info:
            ip = item[4][0]
            if not is_fake_or_private_ip(ip):
                return True
        return False
    except socket.gaierror:
        return False


def dns_prefilter_check_sync(domain: str, timeout_sec=DNS_PREFILTER_TIMEOUT) -> dict:
    """DNS 预筛：本地拦截已注册域名

    Returns:
        {"ok": True, "registered": True, "method": "dns_soa"}      # 有 DNS 记录 → 已注册
        {"ok": True, "registered": False, "method": "no_dns"}     # 无 DNS 记录 → 需 RDAP 进一步证实
        {"ok": False, "error": "..."}                              # DNS 查询失败（罕见）
    """
    # 步骤 1：优先查 SOA 记录
    try:
        import dns.resolver
        resolver = dns.resolver.Resolver()
        resolver.timeout = timeout_sec
        resolver.lifetime = timeout_sec
        resolver.resolve(domain, 'SOA')
        return {"ok": True, "registered": True, "method": "dns_soa"}
    except ImportError:
        pass
    except dns.resolver.NXDOMAIN:
        return {"ok": True, "registered": False, "method": "no_dns"}
    except (dns.resolver.NoNameservers, dns.resolver.NoAnswer):
        # Clash 拦截 SOA 导致 NoNameservers / NoAnswer 时 pass 降级到 A/AAAA，防假阳性
        pass
    except Exception:
        pass

    # 如果 DNS 发生劫持（NXDOMAIN 被返回非私有公网 IP），则 A/AAAA 检查不可信，直接判定为没有 DNS 记录，回退到 RDAP/WHOIS
    if is_dns_hijacked():
        return {"ok": True, "registered": False, "method": "no_dns"}

    # 步骤 2：查 A 记录（IPv4）
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout_sec)
    try:
        if _dns_check_one(domain):
            return {"ok": True, "registered": True, "method": "dns_a"}
        # 步骤 3：查 AAAA 记录（IPv6）
        try:
            info = socket.getaddrinfo(domain, None, socket.AF_INET6, socket.SOCK_STREAM)
            has_real_ip = False
            for item in info:
                ip = item[4][0]
                if not is_fake_or_private_ip(ip):
                    has_real_ip = True
                    break
            if has_real_ip:
                return {"ok": True, "registered": True, "method": "dns_aaaa"}
        except socket.gaierror:
            pass
        # 全部无记录 → 疑似可注册
        return {"ok": True, "registered": False, "method": "no_dns"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:80]}"}
    finally:
        socket.setdefaulttimeout(old_timeout)


async def dns_prefilter_check(domain: str, timeout_sec=DNS_PREFILTER_TIMEOUT) -> dict:
    """异步包装 dns_prefilter_check_sync"""
    return await asyncio.to_thread(dns_prefilter_check_sync, domain, timeout_sec)


# ================== 多 Provider 熔断 + 故障转移 ==================
class ProviderStatus:
    """单个数据源的健康状态机（电路 breaker 模式）"""
    def __init__(self, name: str):
        self.name = name
        self.consecutive_errors = 0
        self.rate_limited_until = 0.0  # time.time() 时间戳，0=未限流
        self.cooldown_until = 0.0     # 错误熔断冷却截止
        self.total_success = 0
        self.total_fail = 0
        self.total_rate_limit = 0
        self.last_error = ""

    def is_available(self) -> bool:
        now = time.time()
        if now < self.rate_limited_until:
            return False
        if now < self.cooldown_until:
            return False
        return True

    def remaining_cooldown(self) -> float:
        """返回还需等待秒数（0 表示可用）"""
        now = time.time()
        return max(0, max(self.rate_limited_until, self.cooldown_until) - now)

    def record_success(self):
        self.consecutive_errors = 0
        self.total_success += 1

    def record_rate_limit(self, retry_after_sec=PROVIDER_BREAKER_RATE_LIMIT_COOLDOWN):
        self.rate_limited_until = time.time() + retry_after_sec
        self.consecutive_errors += 1
        self.total_rate_limit += 1

    def record_error(self, err_msg: str = ""):
        self.consecutive_errors += 1
        self.total_fail += 1
        self.last_error = err_msg[:60]
        if self.consecutive_errors >= PROVIDER_BREAKER_ERROR_THRESHOLD:
            self.cooldown_until = time.time() + PROVIDER_BREAKER_ERROR_COOLDOWN


# 全局 provider 状态注册表
PROVIDER_REGISTRY: dict = {}

def get_provider_status(name: str) -> ProviderStatus:
    if name not in PROVIDER_REGISTRY:
        PROVIDER_REGISTRY[name] = ProviderStatus(name)
    return PROVIDER_REGISTRY[name]


def pick_available_provider(chain: list) -> str | None:
    """从链中选第一个可用的 provider 名称，全不可用返回 None"""
    for name in chain:
        s = get_provider_status(name)
        if s.is_available():
            return name
    return None


def provider_health_report() -> str:
    """打印所有 provider 的健康快照"""
    lines = []
    for name, s in PROVIDER_REGISTRY.items():
        cooldown = s.remaining_cooldown()
        status = "✓" if s.is_available() else f"⏸{cooldown:.0f}s"
        lines.append(f"    {name:14s} {status}  ok={s.total_success} fail={s.total_fail} rl={s.total_rate_limit}")
    return "\n".join(lines)


# ================== 新数据源 1：Botoi（免 Key，补位用） ==================
def botoi_query_sync(domain: str, timeout_sec=8) -> dict:
    """Botoi 免费 API：5 req/min, 100 req/day, 无需 Key
    返回格式: {"ok": True/False, "status": 200/404, "registered": bool, "registrar": str}
    """
    if not BOTOI_ENDPOINT:
        return {"ok": False, "error": "no_endpoint"}
    try:
        import urllib.request, urllib.error
        req = urllib.request.Request(
            BOTOI_ENDPOINT,
            data=json.dumps({"domain": domain}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body)
        if not data.get("success"):
            return {"ok": False, "error": f"api_fail: {str(data)[:60]}"}
        d = data.get("data", {})
        # 防御性：botoi 在其内部 RDAP 出错时（如对 com 域返回 403）会盲报 available=true
        # 检测到这种情况：note 含 "RDAP returned" → 视为错误，跳到下个 provider
        note = d.get("note", "")
        if note and ("RDAP returned" in note or "error" in note.lower()):
            return {"ok": False, "error": f"unreliable_response: {note[:60]}"}
        available = d.get("available")
        if available is None:
            return {"ok": False, "error": f"no_available: {str(d)[:60]}"}
        return {
            "ok": True,
            "status": 404 if available else 200,
            "registered": not available,
            "registrar": d.get("registrar", ""),
        }
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return {"ok": False, "error": "rate_limited", "status": 429}
        if e.code in (403, 422):
            return {"ok": False, "error": f"http_{e.code}"}
        return {"ok": False, "error": f"http_{e.code}"}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"url_err: {str(e.reason)[:60]}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:50]}"}


async def botoi_query(domain: str, timeout_sec=8) -> dict:
    return await asyncio.to_thread(botoi_query_sync, domain, timeout_sec)


# ================== 新数据源 2：Porkbun（需 Key，real price） ==================
def porkbun_query_sync(domain: str, timeout_sec=10) -> dict:
    """Porkbun 官方 API：免费注册就给 Key，查询免费，返回真实注册价
    POST https://api.porkbun.com/api/json/v3/domain/checkDomain/{domain}
    Body: {"apikey": "...", "secretapikey": "..."}
    """
    if not PORKBUN_KEY or not PORKBUN_SECRET:
        return {"ok": False, "error": "missing_credentials"}
    try:
        import urllib.request, urllib.error
        url = f"{PORKBUN_ENDPOINT}/{domain}"
        body = json.dumps({"apikey": PORKBUN_KEY, "secretapikey": PORKBUN_SECRET}).encode("utf-8")
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        status = data.get("status", "")
        if status != "SUCCESS":
            return {"ok": False, "error": f"api_status: {status}: {data.get('message', '')[:60]}"}
        response = data.get("response", {})
        avail = response.get("avail")
        if avail is None:
            return {"ok": False, "error": "no_avail_field"}
        # price 是字符串 "12.34"，单位 USD
        price_str = response.get("price", "")
        try:
            price_usd = float(price_str) if price_str else None
        except (TypeError, ValueError):
            price_usd = None
        # 溢价：价格异常高 或 response.premium 字段
        is_premium = bool(response.get("premium")) or (price_usd is not None and price_usd > 50)
        return {
            "ok": True,
            "status": 404 if avail == "yes" else 200,
            "available": avail == "yes",
            "price_usd": price_usd,
            "currency": "USD",
            "premium": is_premium,
        }
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        if e.code == 429:
            return {"ok": False, "error": "rate_limited", "status": 429}
        if e.code == 401:
            return {"ok": False, "error": f"unauthorized: {body[:60]}"}
        return {"ok": False, "error": f"http_{e.code}: {body[:60]}"}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"url_err: {str(e.reason)[:60]}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:50]}"}


async def porkbun_query(domain: str, timeout_sec=10) -> dict:
    return await asyncio.to_thread(porkbun_query_sync, domain, timeout_sec)


# ================== 新数据源 3：Domainr（RapidAPI，10k free/month） ==================
def domainr_query_sync(domain: str, timeout_sec=10) -> dict:
    """Domainr via RapidAPI: 10k free requests/month
    GET https://domainr.p.rapidapi.com/v2/status?domain=foo.com
    Header: X-RapidAPI-Key, X-RapidAPI-Host
    Response: {"status": [{"domain": "...", "status": "undelegated inactive|active|...", "summary": "..."}]}
    状态值: "active"=已注册, "undelegated" 或 "inactive"=可注册, "parked"=已注册, "marketed"=可注册但溢价
    """
    if not DOMAINR_RAPIDAPI_KEY:
        return {"ok": False, "error": "missing_credentials"}
    try:
        import urllib.request, urllib.error
        from urllib.parse import urlencode
        url = f"{DOMAINR_ENDPOINT}?{urlencode({'domain': domain})}"
        req = urllib.request.Request(url, headers={
            "X-RapidAPI-Key": DOMAINR_RAPIDAPI_KEY,
            "X-RapidAPI-Host": "domainr.p.rapidapi.com",
        })
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        status_list = data.get("status", [])
        if not status_list:
            return {"ok": False, "error": "no_status_field"}
        s = status_list[0]
        summary = s.get("summary", "").lower()
        status_str = s.get("status", "").lower()
        # Domainr 状态映射：
        # active | parked | reserved | claimed = 已注册
        # undelegated | inactive | marketed | available = 可注册（marketed=溢价）
        REGISTERED = {"active", "parked", "reserved", "claimed", "transferable", "preregister"}
        if summary in REGISTERED or any(k in status_str for k in REGISTERED):
            return {"ok": True, "status": 200, "available": False, "summary": summary}
        if "market" in summary or "premium" in status_str or "premium" in summary:
            return {"ok": True, "status": 404, "available": True, "premium": True, "summary": summary}
        # 默认：undelegated / inactive / unknown
        return {"ok": True, "status": 404, "available": True, "premium": False, "summary": summary}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        if e.code == 429:
            return {"ok": False, "error": "rate_limited", "status": 429}
        if e.code == 401 or e.code == 403:
            return {"ok": False, "error": f"unauthorized: {body[:60]}"}
        if e.code == 404:
            # 404 = 域名后缀不支持 Domainr
            return {"ok": False, "error": f"unsupported_tld"}
        return {"ok": False, "error": f"http_{e.code}: {body[:60]}"}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"url_err: {str(e.reason)[:60]}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:50]}"}


async def domainr_query(domain: str, timeout_sec=10) -> dict:
    return await asyncio.to_thread(domainr_query_sync, domain, timeout_sec)


# ================== 新数据源 4：WhoisFreaks（500 free credits） ==================
def whoisfreaks_query_sync(domain: str, timeout_sec=10) -> dict:
    """WhoisFreaks: 500 free credits, no card required
    GET https://api.whoisfreaks.com/v1.0/domain/availability?domain=foo.com&apiKey=KEY
    Response: [{"domain": "...", "domainAvailability": "available"|"registered"}]
    """
    if not WHOISFREAKS_KEY:
        return {"ok": False, "error": "missing_credentials"}
    try:
        import urllib.request, urllib.error
        from urllib.parse import urlencode
        url = f"{WHOISFREAKS_ENDPOINT}?{urlencode({'domain': domain, 'apiKey': WHOISFREAKS_KEY})}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not isinstance(data, list) or not data:
            return {"ok": False, "error": f"unexpected: {str(data)[:60]}"}
        item = data[0]
        avail = item.get("domainAvailability", "").lower()
        if avail == "available":
            return {"ok": True, "status": 404, "available": True}
        if avail == "registered":
            return {"ok": True, "status": 200, "available": False}
        return {"ok": False, "error": f"unknown_avail: {avail}"}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        if e.code == 429:
            return {"ok": False, "error": "rate_limited", "status": 429}
        if e.code == 401 or e.code == 403:
            return {"ok": False, "error": f"unauthorized: {body[:60]}"}
        return {"ok": False, "error": f"http_{e.code}: {body[:60]}"}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"url_err: {str(e.reason)[:60]}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:50]}"}


async def whoisfreaks_query(domain: str, timeout_sec=10) -> dict:
    return await asyncio.to_thread(whoisfreaks_query_sync, domain, timeout_sec)


# Provider 调用分发（统一接口）
async def call_provider(name: str, domain: str) -> dict:
    """按 provider 名调用对应查询函数，统一熔断状态更新"""
    status = get_provider_status(name)
    if not status.is_available():
        return {"ok": False, "error": f"circuit_open: cooldown={status.remaining_cooldown():.0f}s", "_skip": True}

    # 路由
    if name == "botoi":
        res = await botoi_query(domain)
    elif name == "porkbun":
        res = await porkbun_query(domain)
    elif name == "domainr":
        res = await domainr_query(domain)
    elif name == "whoisfreaks":
        res = await whoisfreaks_query(domain)
    elif name == "godaddypublic":
        res = await godaddy_public_batch_query([domain])
        res = res.get(domain, {"ok": False, "error": "no_result"})
    else:
        return {"ok": False, "error": f"unknown_provider: {name}"}

    # 熔断状态更新
    if not res.get("ok"):
        err = res.get("error", "")
        if "rate_limited" in err or res.get("status") == 429:
            status.record_rate_limit()
        else:
            status.record_error(err)
    else:
        status.record_success()
    return res


# 默认 Provider 链路（按优先级排序）
# 零配置可用：rdap / godaddypublic / botoi（不可靠，慎用）
# 需 Key：porkbun / domainr / whoisfreaks
# botoi 排在最后：他们的 RDAP 后端经常 403 然后盲报 available，极不可靠
DEFAULT_PROVIDER_CHAIN = ["rdap", "godaddypublic", "porkbun", "domainr", "whoisfreaks", "botoi"]


async def check_domain_with_chain(session, full_domain: str, suffix: str, semaphore,
                                   chain: list = None) -> dict:
    """Provider 链路查询：依次尝试 chain 中的 provider，失败/限流自动跳到下一个

    失败转移规则：
    - 任一 provider 拿到 ok=True 即返回
    - 限流（429）→ 记录熔断 → 立刻跳到下一个
    - 业务错误（domain 不支持该 provider）→ 立刻跳到下一个
    - 网络错误 → 计入连续错误，N 次后熔断 → 跳到下一个

    Returns: 标准 result dict
    """
    chain = chain or DEFAULT_PROVIDER_CHAIN
    result = {
        "domain": full_domain,
        "suffix": suffix,
        "status": "error",
        "premium": False,
        "premiumReason": "",
        "method": "all_failed",
        "detail": "",
        "price_usd": None,
        "currency": "",
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # Stage 0: DNS 预筛（与原 check_domain 一致）
    if DNS_PREFILTER_ENABLED:
        dns_res = await dns_prefilter_check(full_domain, DNS_PREFILTER_TIMEOUT)
        if dns_res.get("ok") and dns_res.get("registered"):
            result["status"] = "taken"
            result["method"] = f"dns_prefilter({dns_res.get('method', '?')})"
            return result

    # 收集每个 provider 的尝试结果（用于 detail）
    attempts = []

    for provider_name in chain:
        # 跳过未配置 Key 的 provider
        if provider_name == "porkbun" and (not PORKBUN_KEY or not PORKBUN_SECRET):
            continue
        if provider_name == "domainr" and not DOMAINR_RAPIDAPI_KEY:
            continue
        if provider_name == "whoisfreaks" and not WHOISFREAKS_KEY:
            continue

        # RDAP 走多源投票（在 provider 框架内单独处理）
        if provider_name == "rdap":
            rdap_res = await _check_rdap_via_chain(session, full_domain, suffix, semaphore)
            attempts.append(("rdap", rdap_res))
            if rdap_res.get("ok") and rdap_res.get("status") in (200, 404):
                result["status"] = "available" if rdap_res["status"] == 404 else "taken"
                result["method"] = "rdap"
                if result["status"] == "available":
                    result["premium"] = is_likely_premium(full_domain)
                    result["premiumReason"] = get_premium_reason(full_domain) or ""
                result["detail"] = ""
                get_provider_status("rdap").record_success()
                return result
            # RDAP 失败：继续到下一个 provider
            get_provider_status("rdap").record_error(rdap_res.get("error", "rdap_fail"))
            continue

        # 其他 provider 直接调用
        res = await call_provider(provider_name, full_domain)
        attempts.append((provider_name, res))

        if res.get("ok") and res.get("status") in (200, 404):
            result["status"] = "available" if res["status"] == 404 else "taken"
            result["method"] = provider_name
            # 价格 / 溢价
            if res.get("price_usd") is not None:
                result["price_usd"] = res["price_usd"]
                result["currency"] = res.get("currency", "USD")
            if res.get("premium"):
                result["premium"] = True
                result["premiumReason"] = f"{provider_name}返回溢价信号"
            elif result["status"] == "available":
                result["premium"] = is_likely_premium(full_domain)
                result["premiumReason"] = get_premium_reason(full_domain) or ""
            return result
        # 失败：继续
        elif res.get("_skip"):
            # 熔断中：直接跳过
            pass
        # 限流/错误已由 call_provider 内部记录到熔断

    # 全部 provider 都失败
    err_summary = []
    for name, r in attempts[:5]:
        if not r.get("ok"):
            err = r.get("error", "unknown")
            err_summary.append(f"{name}:{err[:30]}")
    result["detail"] = "|".join(err_summary) if err_summary else "no_attempts"
    return result


async def _check_rdap_via_chain(session, full_domain: str, suffix: str, semaphore) -> dict:
    """复用原 check_domain 的 RDAP 多源投票逻辑，但返回标准 dict"""
    # 负缓存：已知 TLD 无 RDAP 端点（如 .cn），跳过 RDAP 直接返回错误
    # 调用方会继续尝试下一个 provider（如 whois 43 端口）
    if is_no_rdap(suffix):
        return {"ok": False, "error": "tld_no_rdap"}
    sources = RDAP_SOURCES.get(suffix, RDAP_SOURCES.get("com", []))
    if not sources:
        return {"ok": False, "error": "no_rdap_sources"}
    primary = sources[0]

    async def _q(url):
        if url.startswith("whois://"):
            return await query_one_source(session, url + "/" + full_domain, semaphore, timeout_sec=6)
        return await query_one_source(session, url + full_domain, semaphore, timeout_sec=6)

    primary_res = await query_with_retry(lambda: _q(primary), max_retries=2)
    if primary_res.get("ok") and primary_res.get("status") in (200, 404):
        return primary_res

    # 多源投票
    async def _one(s):
        if s.startswith("whois://"):
            return await query_one_source(session, s + "/" + full_domain, semaphore)
        return await query_one_source(session, s + full_domain, semaphore)

    tasks = [query_with_retry(lambda s=s: _one(s), max_retries=2) for s in sources]
    responses = await asyncio.gather(*tasks)
    available = [r for r in responses if r.get("ok") and r.get("status") == 404]
    taken = [r for r in responses if r.get("ok") and r.get("status") == 200]
    if taken:
        return {"ok": True, "status": 200}
    if available:
        return {"ok": True, "status": 404}
    errors = [r for r in responses if not r.get("ok")]
    return {"ok": False, "error": "|".join(e.get("error", "?") for e in errors[:3])}


# ================== IANA RDAP Bootstrap 动态路由（借鉴 saidutt46/domain-check） ==================
# IANA 官方维护的 1200+ TLD 的 RDAP 服务器列表
# 启动时拉取一次，本地缓存，避免硬编码
IANA_BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"
IANA_BOOTSTRAP_CACHE = "iana_bootstrap_cache.json"
IANA_BOOTSTRAP_TTL = 86400 * 7  # 7 天缓存

# 已知 IANA 路由不准确的 TLD 覆盖（手动指定更可靠的查询方式）
# 格式: tld -> [url_or_whois_uri, ...]
TLD_OVERRIDES = {
    "cn": ["whois://whois.cnnic.cn:43"],  # CNNIC 自有 RDAP SSL 失败，用 whois 端口 43
    "com.cn": ["whois://whois.cnnic.cn:43"],
    "net.cn": ["whois://whois.cnnic.cn:43"],
    "org.cn": ["whois://whois.cnnic.cn:43"],
    "edu.cn": ["whois://whois.cnnic.cn:43"],
    "gov.cn": ["whois://whois.cnnic.cn:43"],
    "xn--fiqs8s": ["whois://whois.cnnic.cn:43"],  # .中国
}

# 合并 IANA bootstrap 到 RDAP_SOURCES
def _parse_iana_bootstrap(data: dict) -> dict:
    """解析 IANA Bootstrap JSON → {tld: [rdap_urls]}

    实际格式（IANA 官方）:
        {"services": [
            [["kg"], ["http://rdap.cctld.kg/"]],     # 单个 TLD
            [["tw"], ["https://ccrdap.twnic.tw/tw/"]],
            [["samsung", "xn--cg4bki"], ["https://..."]]  # 多个 TLD 共用
        ]}
    """
    result = {}
    for entry in data.get("services", []):
        if not isinstance(entry, list) or len(entry) < 2:
            continue
        tlds = entry[0]      # ["kg"] 或 ["samsung", "xn--cg4bki"]
        servers = entry[1]   # ["http://rdap.cctld.kg/"] 字符串列表
        # 兼容可能存在 dict 的老格式
        urls = []
        for s in servers:
            if isinstance(s, str):
                urls.append(s)
            elif isinstance(s, dict) and "top-most" in s:
                urls.append(s["top-most"])
        for tld in tlds:
            tld_clean = str(tld).lstrip(".").lower()
            if tld_clean and urls:
                # 保留多个 RDAP 源（多源投票用）
                if tld_clean not in result:
                    result[tld_clean] = urls
                else:
                    # 合并去重
                    existing = set(result[tld_clean])
                    for u in urls:
                        if u not in existing:
                            result[tld_clean].append(u)
                            existing.add(u)
    return result


def load_iana_bootstrap_sync(force_refresh: bool = False) -> dict:
    """从 IANA 官方或本地缓存加载 RDAP bootstrap 列表

    Returns:
        {tld: [rdap_urls]} 字典
    """
    import json as _json

    # 1) 优先用本地缓存
    if not force_refresh and os.path.exists(IANA_BOOTSTRAP_CACHE):
        try:
            with open(IANA_BOOTSTRAP_CACHE, "r", encoding="utf-8") as f:
                cache = _json.load(f)
            cache_age = time.time() - cache.get("_cached_at", 0)
            if cache_age < IANA_BOOTSTRAP_TTL and cache.get("data"):
                return _parse_iana_bootstrap(cache["data"])
        except Exception:
            pass  # 缓存损坏，刷新

    # 2) 从 IANA 拉取
    try:
        import urllib.request as _ur
        req = _ur.Request(IANA_BOOTSTRAP_URL, headers={"User-Agent": "Mozilla/5.0 DomainChecker/4.0"})
        with _ur.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
        data = _json.loads(raw)
        # 写缓存
        try:
            with open(IANA_BOOTSTRAP_CACHE, "w", encoding="utf-8") as f:
                _json.dump({"_cached_at": time.time(), "data": data}, f)
        except Exception:
            pass
        return _parse_iana_bootstrap(data)
    except Exception as e:
        print(f"  [IANA] 拉取 bootstrap 失败: {type(e).__name__}: {str(e)[:80]}")
        return {}


async def load_iana_bootstrap(force_refresh: bool = False) -> dict:
    """异步包装 load_iana_bootstrap_sync"""
    return await asyncio.to_thread(load_iana_bootstrap_sync, force_refresh)


def build_rdap_sources(iana_data: dict = None) -> dict:
    """合并 IANA bootstrap + 手动覆盖 + 硬编码回退，生成最终的 RDAP_SOURCES

    Args:
        iana_data: 来自 load_iana_bootstrap() 的数据，None 时自动加载

    Returns:
        合并后的 {tld: [urls]} 字典（直接赋值给 RDAP_SOURCES）
    """
    if iana_data is None:
        iana_data = load_iana_bootstrap_sync()

    # 以硬编码的 RDAP_SOURCES 为基础（保留可靠的多源）
    merged = dict(RDAP_SOURCES)

    # 用 IANA 数据填补未配置的 TLD
    added = 0
    for tld, urls in iana_data.items():
        if tld not in merged:
            merged[tld] = urls
            added += 1

    # 应用手动覆盖
    for tld, urls in TLD_OVERRIDES.items():
        merged[tld] = urls

    return merged


# ================== 指数退避重试（借鉴 beast-domain-checker） ==================
# 429/503 → 等 1.5s → 3s → 6s → 最多 3 次重试
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 1.5  # 退避基数（秒）


async def query_with_retry(query_func, max_retries=DEFAULT_MAX_RETRIES, backoff_base=DEFAULT_BACKOFF_BASE):
    """指数退避重试包装器

    Args:
        query_func: async callable，无参调用返回 {"ok": bool, "status": int, "error": str}
        max_retries: 最多重试次数（含首次）
        backoff_base: 退避基数，等待时间 = backoff_base ^ attempt

    Returns:
        与 query_func 相同格式的 dict
    """
    last_result = None
    for attempt in range(1, max_retries + 1):
        result = await query_func()
        last_result = result
        if not result.get("ok"):
            # 失败：根据错误类型决定是否重试
            error = result.get("error", "")
            # 超时直接返回，不作任何重试，避免拖慢查询速度造成 Nginx 504 Gateway Timeout
            if "timeout" in error.lower():
                return result
            # 网络类错误：client_err / connection_reset → 重试
            if any(kw in error.lower() for kw in ("client_err", "connection", "reset")):
                if attempt < max_retries:
                    wait = backoff_base ** attempt
                    await asyncio.sleep(wait)
                    continue
            return result
        # 成功：但 429/503 触发重试
        status = result.get("status", 0)
        if status in (429, 503):
            if attempt < max_retries:
                wait = min(backoff_base ** attempt * 1.5, 10)
                await asyncio.sleep(wait)
                continue
        return result
    return last_result or {"ok": False, "error": "max_retries_exceeded"}


# ================== GoDaddy 批量 API ==================

# 各 TLD 的"普通注册价"上限（USD），超过此价则视为溢价域名
# 数据来源：GoDaddy 公开定价 + 主流注册商均价
# .com 正常 $11.99/首年, $14.99/续费
# .cn 正常 ¥29 / $4-5
# .io 正常 $33-50
# .ai 正常 $80-90
# .net 正常 $12.99
# .org 正常 $11.99
GODADDY_PREMIUM_THRESHOLDS = {
    "com": 50,    # $11.99 正常，>50 视为溢价
    "net": 50,
    "org": 50,
    "info": 50,
    "io": 200,    # $33-50 正常
    "ai": 500,    # $80-90 正常
    "app": 100,
    "dev": 100,
    "co": 100,
    "me": 80,
    "cc": 60,
    "tv": 100,
    "biz": 60,
    "us": 50,
    "mobi": 80,
    "xyz": 50,
    "cn": 200,    # ¥29 正常
    "com.cn": 200,
    "net.cn": 200,
    "org.cn": 200,
}
# 通用兜底阈值
DEFAULT_PREMIUM_THRESHOLD = 100


def parse_godaddy_response(domain: str, data: dict) -> dict:
    """解析 GoDaddy 响应为内部标准格式
    返回: {"ok": True, "status": 200/404, "price": int(微单位), "currency": str, "premium": bool}
    """
    if not isinstance(data, dict):
        return {"ok": False, "error": f"unexpected_response: {type(data).__name__}"}

    available = data.get("available")
    if available is None:
        return {"ok": False, "error": f"no_available_field: {str(data)[:100]}"}

    price_micro = data.get("price")  # 微单位，除以 1,000,000
    currency = data.get("currency", "USD")
    period = data.get("period", 1)

    price_usd = None
    if price_micro is not None:
        price_usd = price_micro / 1_000_000

    # 溢价判断：未注册但价格异常高
    # 两道保险：1) GoDaddy 价格 > 阈值  2) 启发式规则命中（get_premium_reason）
    # 只要任一命中即标为溢价
    premium = False
    premium_reasons = []
    if available and price_usd is not None:
        suffix = domain.rsplit(".", 1)[-1].lower()
        threshold = GODADDY_PREMIUM_THRESHOLDS.get(suffix, DEFAULT_PREMIUM_THRESHOLD)
        if price_usd > threshold:
            premium = True
            premium_reasons.append(f"高价${price_usd:.0f}>{threshold}")

    return {
        "ok": True,
        "status": 200 if not available else 404,  # RDAP 风格：200=已注册, 404=可注册
        "available": available,
        "price_usd": price_usd,
        "currency": currency,
        "period": period,
        "premium": premium,
        "premium_reasons": premium_reasons,
    }


def godaddy_batch_query_sync(domains: list, timeout_sec=20) -> dict:
    """同步调用 GoDaddy POST /v1/domains/available，返回 {domain: result_dict}
    单次最多 500 个域名
    """
    if not GODADDY_KEY or not GODADDY_SECRET:
        return {"_error": "missing_credentials", "_message": "请设置环境变量 GODADDY_KEY 和 GODADDY_SECRET"}

    url = f"{GODADDY_BASE}/v1/domains/available?checkType=FULL"
    headers = {
        "Authorization": f"sso-key {GODADDY_KEY}:{GODADDY_SECRET}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    try:
        # 同步 requests 已经被 aiohttp 覆盖，这里用内置 urllib 避免额外依赖
        import urllib.request
        import urllib.error
        req = urllib.request.Request(url, data=json.dumps(domains).encode("utf-8"),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        if e.code == 401:
            return {"_error": "unauthorized", "_message": f"API key 无效或被拒。响应: {body[:200]}"}
        if e.code == 422:
            # 单个域名无效，整体仍可能成功
            return {"_error": "validation_error", "_message": f"请求格式错误: {body[:200]}"}
        if e.code == 429:
            return {"_error": "rate_limited", "_message": "触发限流，请降低并发或稍后重试"}
        return {"_error": f"http_{e.code}", "_message": body[:200]}
    except urllib.error.URLError as e:
        return {"_error": "network_error", "_message": str(e.reason)[:200]}
    except Exception as e:
        return {"_error": f"{type(e).__name__}", "_message": str(e)[:200]}

    # 解析响应
    if isinstance(data, dict) and "domains" in data:
        # 正常批量响应
        results = {}
        for item in data["domains"]:
            d = item.get("domain", "")
            results[d] = parse_godaddy_response(d, item)
        # 处理 errors
        for err in data.get("errors", []):
            d = err.get("domain", "")
            results[d] = {"ok": False, "error": f"{err.get('code','?')}: {err.get('message','')[:80]}"}
        return results
    elif isinstance(data, list):
        # 简单数组响应
        results = {}
        for item in data:
            d = item.get("domain", "")
            results[d] = parse_godaddy_response(d, item)
        return results
    else:
        return {"_error": "unexpected_format", "_message": str(data)[:200]}


async def godaddy_batch_query(domains: list, timeout_sec=20) -> dict:
    """异步包装"""
    return await asyncio.to_thread(godaddy_batch_query_sync, domains, timeout_sec)


def _mk_result(domain: str, status: str, premium: bool, reason: str,
               method: str, detail: str = "",
               price_usd: float = "", currency: str = "",
               confidence: str = None, is_whois: bool = None,
               sources_ok: int = None, sources_total: int = None) -> dict:
    """构造标准结果 dict（统一字段）"""
    # 自动推断 is_whois
    if is_whois is None:
        is_whois = "whois" in method or "whois" in detail
        
    # 自动推断 sources_ok / sources_total
    if sources_ok is None:
        sources_ok = 0 if status == "error" else 1
    if sources_total is None:
        sources_total = 1

    res = {
        "domain": domain,
        "suffix": domain.rsplit(".", 1)[-1] if "." in domain else "",
        "status": status,
        "premium": premium,
        "premiumReason": reason,
        "method": method,
        "detail": detail,
        "price_usd": price_usd,
        "currency": currency,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "is_whois": is_whois,
        "sources_ok": sources_ok,
        "sources_total": sources_total,
    }
    
    if confidence is None:
        res["confidence"] = score_confidence(res)
    else:
        res["confidence"] = confidence
        
    return res


# ================== JSON 导入（GoDaddy 用） ==================
import json
import re


# ================== GoDaddy 公共端点（无需 API Key） ==================
# 来源参考: dorukardahan/domain-search-mcp v1.10.0
# 端点: https://api.godaddy.com/v1/domains/mcp  (JSON-RPC 2.0 over HTTP, SSE 响应)
# 限制: 30 req/min (未官方文档化), 每次最多 1000 个域名
# 优势: 零配置可用，能识别 Premium/Auction 域名
GODADDY_PUBLIC_ENDPOINT = "https://api.godaddy.com/v1/domains/mcp"
GODADDY_PUBLIC_BATCH_SIZE = 100  # 保守批量大小
GODADDY_PUBLIC_TIMEOUT = 8       # 单次请求超时（秒）

# 溢价判定阈值：suggestions 中 Premium 比例
# 经测试，普通可注册长串域名 premium 建议占比约 1/40 = 2.5%
# 真溢价域名 premium 建议占比通常 >10%（如 aigame.io 测出 7/39 = 18%）
PREMIUM_SIGNAL_STRONG = 0.10     # >10% 强信号
PREMIUM_SIGNAL_WEAK = 0.05       # 5-10% 中等信号


def godaddy_public_query_sync(domains: list, timeout_sec=GODADDY_PUBLIC_TIMEOUT) -> dict:
    """调用 GoDaddy 公共 MCP 端点（JSON-RPC over HTTP），无需 API Key

    Args:
        domains: 域名列表，如 ["aibox.com", "aidog.com"]
        timeout_sec: 单次 HTTP 超时

    Returns:
        {domain: {"ok": True, "status": 200/404, "premium": bool, "premium_signal": float, "available": bool}}
        或 {"_error": "...", "_message": "..."}
    """
    if not domains:
        return {}

    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "domains_check_availability",
            "arguments": {
                "domains": ", ".join(domains)
            }
        },
        "id": 1
    }

    try:
        import urllib.request as _ur
        import urllib.error as _ue
        req = _ur.Request(
            GODADDY_PUBLIC_ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "User-Agent": "Mozilla/5.0 DomainChecker/4.0",
            },
            method="POST"
        )
        with _ur.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8")
    except _ue.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        if e.code == 429:
            return {"_error": "rate_limited", "_message": "GoDaddy 公共端点限流 (30 req/min)，请降低并发或稍后重试"}
        if e.code == 403:
            return {"_error": "forbidden", "_message": f"GoDaddy 拒绝请求 (可能需浏览器 User-Agent)。响应: {body[:200]}"}
        return {"_error": f"http_{e.code}", "_message": body[:200]}
    except _ue.URLError as e:
        return {"_error": "network_error", "_message": str(e.reason)[:200]}
    except Exception as e:
        return {"_error": f"{type(e).__name__}", "_message": str(e)[:200]}

    # 解析 SSE 响应: "event: message\ndata: {json}"
    try:
        m = re.search(r"data:\s*(\{.*\})", raw, re.DOTALL)
        if not m:
            return {"_error": "invalid_format", "_message": f"无法解析 SSE 响应: {raw[:200]}"}
        envelope = json.loads(m.group(1))
    except Exception as e:
        return {"_error": "parse_error", "_message": f"JSON 解析失败: {str(e)[:200]}"}

    if "error" in envelope:
        return {"_error": "rpc_error", "_message": envelope.get("error", {}).get("message", "unknown")[:200]}

    result = envelope.get("result", {})
    structured = result.get("structuredContent", {})
    domain_groups = structured.get("domainGroups", [])

    if not domain_groups:
        # 兜底：解析 markdown 文本
        text = ""
        for c in result.get("content", []):
            if c.get("type") == "text":
                text = c.get("text", "")
                break
        return _parse_godaddy_public_markdown(text, domains)

    # 主路径：使用 structuredContent
    results = {}
    for dg in domain_groups:
        domain = dg.get("searchedDomain", "").lower()
        available = dg.get("available", False)
        sugg = dg.get("domains", [])

        # 计算 premium 比例（强信号）
        premium_count = sum(1 for s in sugg if s.get("inventoryType") == "Premium")
        auction_count = sum(1 for s in sugg if s.get("inventoryType") == "Auction")
        total = max(len(sugg), 1)
        premium_ratio = premium_count / total
        auction_ratio = auction_count / total

        # 判定溢价
        premium = False
        premium_reason = ""
        if available:
            if premium_ratio >= PREMIUM_SIGNAL_STRONG or auction_ratio >= 0.05:
                premium = True
                if auction_ratio >= 0.05:
                    premium_reason = f"Auction({auction_count}/{total})"
                else:
                    premium_reason = f"Premium建议({premium_count}/{total},{premium_ratio:.0%})"
            elif premium_ratio >= PREMIUM_SIGNAL_WEAK:
                # 中等信号：标记疑似溢价
                premium = True
                premium_reason = f"Premium建议({premium_count}/{total})"

        results[domain] = {
            "ok": True,
            "status": 404 if available else 200,  # RDAP 风格
            "available": available,
            "premium": premium,
            "premium_reason": premium_reason,
            "premium_signal": premium_ratio,
            "auction_signal": auction_ratio,
            "suggestion_count": total,
        }

    return results


def _parse_godaddy_public_markdown(text: str, original_domains: list) -> dict:
    """兜底：从 markdown 文本中解析（当 structuredContent 缺失时使用）"""
    results = {}
    if not text:
        return {"_error": "empty_response", "_message": "GoDaddy 返回空"}

    # 简化：直接用文本包含判断
    unavailable_match = re.search(r"❌\s*\*\*UNAVAILABLE[^*]*\*\*[^\n]*((?:\n[^\n]+)*)", text)
    available_match = re.search(r"✅\s*\*\*(?:AVAILABLE|STANDARD)[^*]*\*\*[^\n]*((?:\n[^\n]+)*)", text)
    premium_match = re.search(r"💎\s*\*\*PREMIUM[^*]*\*\*[^\n]*((?:\n[^\n]+)*)", text)
    auction_match = re.search(r"🔨\s*\*\*AUCTION[^*]*\*\*[^\n]*((?:\n[^\n]+)*)", text)

    unavailable_section = unavailable_match.group(0).lower() if unavailable_match else ""
    premium_section = premium_match.group(0).lower() if premium_match else ""
    auction_section = auction_match.group(0).lower() if auction_match else ""

    for d in original_domains:
        d_lower = d.lower()
        is_unavailable = d_lower in unavailable_section
        is_premium = d_lower in premium_section
        is_auction = d_lower in auction_section

        available = not is_unavailable

        results[d_lower] = {
            "ok": True,
            "status": 404 if available else 200,
            "available": available,
            "premium": is_premium or is_auction,
            "premium_reason": "Auction" if is_auction else ("Premium" if is_premium else ""),
            "premium_signal": 0.0,
            "auction_signal": 0.0,
            "suggestion_count": 0,
        }

    return results


async def godaddy_public_batch_query(domains: list, timeout_sec=GODADDY_PUBLIC_TIMEOUT) -> dict:
    """异步包装 godaddy_public_query_sync"""
    return await asyncio.to_thread(godaddy_public_query_sync, domains, timeout_sec)


async def check_domain(session, full_domain: str, suffix: str, semaphore) -> dict:
    """Provider 抽象层查询一个域名（DNS 预筛 → RDAP 多源 → WHOIS → GoDaddy）

    优先级链（参考 beast-domain-checker）：
    1. DNS 预筛（5ms）→ 有 DNS 记录直接判 taken
    2. RDAP 多源投票（首选，免费，覆盖 1200+ TLD）
    3. WHOIS 端口 43（备选，免费，用于 .cn 等 IANA 路由不准的 TLD）
    4. GoDaddy 公共端点（可选，增强溢价检测）

    所有 HTTP/RDAP 查询走 query_with_retry() 自动重试
    """
    sources = RDAP_SOURCES.get(suffix, RDAP_SOURCES["com"])
    result = {
        "domain": full_domain,
        "suffix": suffix,
        "status": "error",
        "premium": False,
        "premiumReason": "",
        "method": "all_failed",
        "detail": "",
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "is_whois": False,
        "sources_ok": 0,
        "sources_total": len(sources),
        "confidence": "LOW",
    }

    # ============ Stage 0: DNS 预筛（5ms 本地拦截）============
    # 重要：DNS 预筛命中后必须走 RDAP 二次验证，
    # 因为公共 DNS 常有 wildcard catch-all（广告 IP）会污染"已注册"判定。
    if DNS_PREFILTER_ENABLED:
        dns_res = await dns_prefilter_check(full_domain, DNS_PREFILTER_TIMEOUT)
        if dns_res.get("ok") and dns_res.get("registered"):
            # 二次验证：用 RDAP 主源确认 DNS 预筛结论
            primary = sources[0]
            is_whois_primary = primary.startswith("whois://")
            primary_url = primary + (f"/{full_domain}" if is_whois_primary else full_domain)
            try:
                verify_res = await query_with_retry(
                    lambda: query_one_source(session, primary_url, semaphore, timeout_sec=3),
                    max_retries=1,
                )
                if verify_res["ok"] and verify_res["status"] == 200:
                    # RDAP 确认已注册 → 双源一致，置信度高
                    result["status"] = "taken"
                    result["method"] = f"dns_prefilter({dns_res.get('method', '?')})+rdap_verify"
                    result["is_whois"] = is_whois_primary
                    result["sources_ok"] = 2
                    result["sources_total"] = len(sources)
                    result["confidence"] = score_confidence(result)
                    return result
                if verify_res["ok"] and verify_res["status"] == 404:
                    # RDAP 404 → 推翻 DNS 预筛结论，域名其实可注册（DNS 劫持/通配）
                    result["status"] = "available"
                    result["method"] = f"dns_prefilter_dns_hijack({dns_res.get('method', '?')})"
                    result["premium"] = is_likely_premium(full_domain)
                    result["premiumReason"] = get_premium_reason(full_domain) or ""
                    result["is_whois"] = False
                    result["sources_ok"] = 1
                    result["sources_total"] = len(sources)
                    result["confidence"] = score_confidence(result)
                    result["detail"] = f"DNS 显示有记录但 RDAP 404（疑似 DNS 劫持）：{dns_res.get('method', '?')}"
                    return result
            except Exception:
                # 验证源失败时降级：保留 DNS 结论但置信度低
                pass

            # 验证失败时的降级结果（仍判定 taken，但置信度 LOW）
            result["status"] = "taken"
            result["method"] = f"dns_prefilter_unverified({dns_res.get('method', '?')})"
            result["is_whois"] = False
            result["sources_ok"] = 1
            result["sources_total"] = len(sources)
            result["confidence"] = score_confidence(result)
            result["detail"] = f"DNS 预筛命中但 RDAP 验证失败：{dns_res.get('method', '?')}"
            return result

    # ============ Stage 1: 主源快速判定（带重试）============
    primary = sources[0]
    async def _primary_query():
        if primary.startswith("whois://"):
            return await query_one_source(session, primary + "/" + full_domain, semaphore, timeout_sec=5)
        return await query_one_source(session, primary + full_domain, semaphore, timeout_sec=3)

    primary_res = await query_with_retry(_primary_query, max_retries=2)
    is_whois_primary = primary.startswith("whois://")
    if primary_res["ok"] and primary_res["status"] == 404:
        result["status"] = "available"
        result["premium"] = is_likely_premium(full_domain)
        result["premiumReason"] = get_premium_reason(full_domain) or ""
        result["method"] = "primary"
        result["is_whois"] = is_whois_primary
        result["sources_ok"] = 1
        result["sources_total"] = len(sources)
        result["confidence"] = score_confidence(result)
        return result
    if primary_res["ok"] and primary_res["status"] == 200:
        result["status"] = "taken"
        result["method"] = "primary"
        result["is_whois"] = is_whois_primary
        result["sources_ok"] = 1
        result["sources_total"] = len(sources)
        result["confidence"] = score_confidence(result)
        return result

    # ============ Stage 2: 多源并发投票（带重试）============
    async def _one_source(s):
        if s.startswith("whois://"):
            return await query_one_source(session, s + "/" + full_domain, semaphore, timeout_sec=5)
        return await query_one_source(session, s + full_domain, semaphore, timeout_sec=3)

    tasks = [query_with_retry(lambda s=s: _one_source(s), max_retries=2) for s in sources]
    responses = await asyncio.gather(*tasks)

    available = [r for r in responses if r["ok"] and r["status"] == 404]
    taken = [r for r in responses if r["ok"] and r["status"] == 200]
    errors = [r for r in responses if not r["ok"]]
    strange = [r for r in responses if r["ok"] and r["status"] not in (200, 404)]

    result["is_whois"] = any(s.startswith("whois://") for s in sources)
    result["sources_total"] = len(sources)
    result["sources_ok"] = len(available) + len(taken) + len(strange)

    if taken:
        result["status"] = "taken"
        result["method"] = "consensus"
    elif available:
        result["status"] = "available"
        result["premium"] = is_likely_premium(full_domain)
        result["premiumReason"] = get_premium_reason(full_domain) or ""
        result["method"] = "consensus"
    else:
        # 全部失败
        if strange:
            result["detail"] = "奇怪状态:" + ",".join(str(r["status"]) for r in strange[:3])
        else:
            result["detail"] = "|".join(e["error"] for e in errors[:3])

    result["confidence"] = score_confidence(result)
    return result


def generate_middle_combos(middle_len: int, letters: str) -> list:
    """生成中间部分的字母组合"""
    if middle_len == 0:
        return [""]
    return ["".join(combo) for combo in itertools.product(letters, repeat=middle_len)]


def get_letters(letter_range: str) -> str:
    """从范围字符串获取字母集"""
    all_letters = string.ascii_lowercase
    if letter_range == "all":
        return all_letters
    parts = letter_range.split("-")
    if len(parts) == 2:
        from_c, to_c = parts
        return "".join(c for c in all_letters if from_c <= c <= to_c)
    return all_letters


async def run_checker(args):
    # 全局开关：DNS 预筛
    global DNS_PREFILTER_ENABLED
    DNS_PREFILTER_ENABLED = not getattr(args, "no_dns_prefilter", False)

    # 启动时拉取 IANA bootstrap（一次性，自动缓存 7 天）
    # 不阻塞启动：拉取失败也不影响已有 TLD
    try:
        iana_data = await asyncio.wait_for(
            load_iana_bootstrap(force_refresh=getattr(args, "refresh_iana", False)),
            timeout=10
        )
        if iana_data:
            new_sources = build_rdap_sources(iana_data)
            old_count = len(RDAP_SOURCES)
            RDAP_SOURCES.clear()
            RDAP_SOURCES.update(new_sources)
            print(f"  [IANA] 已加载 {len(iana_data)} 个 TLD 的 RDAP 服务器 (合计 {len(RDAP_SOURCES)} 个, 新增 {len(RDAP_SOURCES) - old_count})")
    except Exception as e:
        print(f"  [IANA] 启动时加载失败（不影响现有 TLD）: {type(e).__name__}: {str(e)[:60]}")

    prefixes = [args.prefix] if args.prefix is not None else [""]
    suffix_custom = args.custom_suffix or ""
    total_len = args.length
    middle_len = total_len - len(prefixes[0]) - len(suffix_custom)

    if middle_len < 1:
        print(f"错误: 域名长度 {total_len} 必须大于前缀+后缀 {len(prefixes[0]) + len(suffix_custom)}")
        return
    if middle_len > 6:
        print(f"警告: 中间 {middle_len} 位组合数极大，建议拆成多批 (例如 --letters a-m)")

    letters = get_letters(args.letters)
    middle_combos = generate_middle_combos(middle_len, letters)
    print(f"\n字母范围: {args.letters} ({len(letters)} 个)")

    # 构造完整域名前缀（不含顶级后缀）
    domain_bases = []
    for prefix in prefixes:
        for middle in middle_combos:
            domain_bases.append(prefix + middle + suffix_custom)

    suffixes = args.suffix
    # 校验
    invalid = [s for s in suffixes if s not in RDAP_SOURCES]
    if invalid:
        print(f"错误: 不支持的后缀: {invalid}。可用: {list(RDAP_SOURCES.keys())}")
        return

    total = len(domain_bases) * len(suffixes)
    print(f"\n{'='*60}")
    print(f"  域名批量查询工具 v3 (RDAP / GoDaddy / Auto)")
    print(f"{'='*60}")
    print(f"  前缀:      '{prefixes[0]}'")
    print(f"  中间长度:  {middle_len} 位")
    print(f"  后缀:      '{suffix_custom}'")
    print(f"  顶级后缀:  {', '.join(suffixes)}")
    print(f"  字母范围:  {args.letters} ({len(letters)} 个)")
    print(f"  组合数:    {len(domain_bases):,} 域名 × {len(suffixes)} 后缀 = {total:,} 次查询")
    print(f"  数据源:    {args.source}")
    if args.source in ("godaddy", "auto"):
        if GODADDY_KEY and GODADDY_SECRET:
            print(f"  GoDaddy:   {GODADDY_BASE}  Key={GODADDY_KEY[:6]}***")
        else:
            print(f"  GoDaddy:   未配置 Key (需设置环境变量 GODADDY_KEY / GODADDY_SECRET)")
    if args.source in ("godaddypublic", "auto"):
        print(f"  GoDaddy 公共: {GODADDY_PUBLIC_ENDPOINT}  (无需 Key)")
    if args.source in ("chain", "botoi", "porkbun", "domainr", "whoisfreaks"):
        # 显示各 provider 状态
        if PORKBUN_KEY and PORKBUN_SECRET:
            print(f"  Porkbun:    {PORKBUN_ENDPOINT}  Key={PORKBUN_KEY[:6]}***")
        else:
            print(f"  Porkbun:    未配置 Key (注册 porkbun.com/account/api 即可免费获取)")
        if DOMAINR_RAPIDAPI_KEY:
            print(f"  Domainr:    {DOMAINR_ENDPOINT}  (RapidAPI)")
        else:
            print(f"  Domainr:    未配置 RapidAPI Key (rapidapi.com 注册可获 10k free/month)")
        if WHOISFREAKS_KEY:
            print(f"  WhoisFreaks:{WHOISFREAKS_ENDPOINT}  Key={WHOISFREAKS_KEY[:6]}***")
        else:
            print(f"  WhoisFreaks:未配置 Key (whoisfreaks.com 注册 500 free credits)")
        print(f"  Botoi:      {BOTOI_ENDPOINT}  (免 Key, 5 req/min, 100 req/day)")
    print(f"  并发数:    {args.workers}")
    print(f"  输出:      {args.output}")
    print(f"{'='*60}\n")

    semaphore = asyncio.Semaphore(args.workers)
    results = []
    stats = {"available": 0, "taken": 0, "error": 0, "premium": 0}
    start_time = time.time()
    last_log = 0

    # ===== 数据源路由 =====
    use_godaddy = False
    use_godaddy_public = False
    if args.source == "godaddy":
        if not GODADDY_KEY or not GODADDY_SECRET:
            print("错误: --source godaddy 需要设置 GODADDY_KEY 和 GODADDY_SECRET 环境变量")
            return
        use_godaddy = True
    elif args.source == "godaddypublic":
        use_godaddy_public = True
    elif args.source == "auto":
        if GODADDY_KEY and GODADDY_SECRET:
            use_godaddy = True
            print("  [auto] GoDaddy Key 已配置 → 通用 TLD 走 GoDaddy 批量，.cn 走 CNNIC whois")
        else:
            use_godaddy_public = True
            print("  [auto] GoDaddy Key 未配置 → 走 GoDaddy 公共端点（零配置 + 智能溢价检测）")

    if use_godaddy:
        # ============ GoDaddy 批量模式 ============
        gd_suffixes = [s for s in suffixes if s != "cn"]
        cn_suffixes = [s for s in suffixes if s == "cn"]

        # 1) GoDaddy 查非 .cn
        if gd_suffixes:
            all_gd = [f"{base}.{s}" for base in domain_bases for s in gd_suffixes]
            print(f"  [GoDaddy] 准备批量查询 {len(all_gd):,} 个域名（{len(gd_suffixes)} 个后缀）")
            for batch_start in range(0, len(all_gd), GODADDY_BATCH_SIZE):
                batch = all_gd[batch_start:batch_start + GODADDY_BATCH_SIZE]
                batch_num = batch_start // GODADDY_BATCH_SIZE + 1
                total_batches = (len(all_gd) + GODADDY_BATCH_SIZE - 1) // GODADDY_BATCH_SIZE
                print(f"  [GoDaddy] 批次 {batch_num}/{total_batches}: {len(batch)} 个...")
                br = await godaddy_batch_query(batch)
                if "_error" in br:
                    print(f"  [GoDaddy] 批次失败: {br['_error']} - {br.get('_message','')[:200]}")
                    for d in batch:
                        results.append(_mk_result(d, "error", False, "", "godaddy_failed", br.get("_error","")))
                        stats["error"] += 1
                    continue
                for d in batch:
                    r = br.get(d)
                    if r is None or not r.get("ok"):
                        err_msg = (r or {}).get("error", "no_data")
                        results.append(_mk_result(d, "error", False, "", "godaddy_missing", err_msg))
                        stats["error"] += 1
                        continue
                    available = r.get("available", False)
                    is_premium = r.get("premium", False)
                    heuristic = get_premium_reason(d)
                    if not is_premium and heuristic:
                        is_premium = True
                    if available:
                        stats["available"] += 1
                        if is_premium:
                            stats["premium"] += 1
                        price_str = f"${r.get('price_usd','')}" if r.get('price_usd') else ""
                        print(f"  \033[92m✓ 可注册{'(溢价)' if is_premium else ''}\033[0m  {d}  {price_str}")
                    else:
                        stats["taken"] += 1
                    results.append(_mk_result(
                        d,
                        "available" if available else "taken",
                        is_premium,
                        heuristic or ("高价" if r.get("premium") else ""),
                        "godaddy",
                        "",
                        r.get("price_usd", ""),
                        r.get("currency", ""),
                    ))

        # 2) .cn 走 CNNIC whois
        if cn_suffixes:
            print(f"  [CNNIC whois] 准备查询 .cn 域名...")
            connector = aiohttp.TCPConnector(limit=args.workers * 2, ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                tasks = []
                for base in domain_bases:
                    for s in cn_suffixes:
                        full = f"{base}.{s}"
                        tasks.append(check_domain(session, full, s, semaphore))
                completed = 0
                total_cn = len(tasks)
                for coro in asyncio.as_completed(tasks):
                    result = await coro
                    result["price_usd"] = ""
                    result["currency"] = ""
                    results.append(result)
                    completed += 1
                    if result["status"] == "available":
                        stats["available"] += 1
                        if result["premium"]:
                            stats["premium"] += 1
                    elif result["status"] == "taken":
                        stats["taken"] += 1
                    else:
                        stats["error"] += 1
                    if completed - last_log >= max(1, total_cn // 20) or completed == total_cn:
                        pct = completed / total_cn * 100
                        print(f"\r  [CNNIC] {pct:.0f}% | {completed}/{total_cn} | ✓{stats['available']} ✗{stats['taken']} ?{stats['error']}", end="", flush=True)
                        last_log = completed
                print()
    elif use_godaddy_public:
        # ============ GoDaddy 公共端点批量模式（无需 Key） ============
        # 支持 .com .net .org .cn .io .ai .app .dev 等 GoDaddy 收录的 TLD
        # .cn 等 RDAP-only 后缀走 CNNIC whois fallback
        gd_suffixes = [s for s in suffixes if s != "cn"]
        cn_suffixes = [s for s in suffixes if s == "cn"]

        # 1) GoDaddy 公共端点查通用 TLD
        if gd_suffixes:
            all_gd = [f"{base}.{s}" for base in domain_bases for s in gd_suffixes]
            print(f"  [GoDaddy 公共] 准备批量查询 {len(all_gd):,} 个域名（{len(gd_suffixes)} 个后缀，batch={GODADDY_PUBLIC_BATCH_SIZE}）")
            for batch_start in range(0, len(all_gd), GODADDY_PUBLIC_BATCH_SIZE):
                batch = all_gd[batch_start:batch_start + GODADDY_PUBLIC_BATCH_SIZE]
                batch_num = batch_start // GODADDY_PUBLIC_BATCH_SIZE + 1
                total_batches = (len(all_gd) + GODADDY_PUBLIC_BATCH_SIZE - 1) // GODADDY_PUBLIC_BATCH_SIZE
                print(f"  [GoDaddy 公共] 批次 {batch_num}/{total_batches}: {len(batch)} 个...")
                br = await godaddy_public_batch_query(batch, GODADDY_PUBLIC_TIMEOUT)
                if "_error" in br:
                    err_code = br.get("_error", "unknown")
                    err_msg = br.get("_message", "")
                    print(f"  [GoDaddy 公共] 批次失败: {err_code} - {err_msg[:200]}")
                    if err_code == "rate_limited":
                        # 限流：降级到 RDAP 模式
                        print("  [GoDaddy 公共] 触发限流，剩余批次回退到 RDAP 模式")
                        # 补完剩余批次：把已处理域名跳过
                        processed = {r["domain"] for r in results}
                        remaining = [d for d in all_gd[batch_start:] if d not in processed]
                        # 走 RDAP 模式补查
                        connector = aiohttp.TCPConnector(limit=args.workers * 2, ssl=False)
                        async with aiohttp.ClientSession(connector=connector,
                                                          headers={"User-Agent": "Mozilla/5.0 DomainChecker/4.0",
                                                                   "Accept": "application/json"}) as session:
                            for d in remaining:
                                suf = d.rsplit(".", 1)[-1]
                                r = await check_domain(session, d, suf, semaphore)
                                r["price_usd"] = ""
                                r["currency"] = ""
                                results.append(r)
                                completed = len(results)
                                if r["status"] == "available":
                                    stats["available"] += 1
                                    if r["premium"]: stats["premium"] += 1
                                elif r["status"] == "taken":
                                    stats["taken"] += 1
                                else:
                                    stats["error"] += 1
                        break
                    else:
                        # 其它错误：把批次标为 error 但继续
                        for d in batch:
                            results.append(_mk_result(d, "error", False, "", "godaddypublic_failed", err_code))
                            stats["error"] += 1
                        continue

                # 正常处理响应
                for d in batch:
                    r = br.get(d.lower())
                    if r is None or not r.get("ok"):
                        err_msg = (r or {}).get("error", "no_data")
                        results.append(_mk_result(d, "error", False, "", "godaddypublic_missing", err_msg))
                        stats["error"] += 1
                        continue
                    available = r.get("available", False)
                    is_premium = r.get("premium", False)
                    gd_reason = r.get("premium_reason", "")
                    heuristic = get_premium_reason(d)
                    if not is_premium and heuristic:
                        # GoDaddy 未标溢价，但启发式判定为溢价：合并判定
                        is_premium = True
                        final_reason = heuristic
                    elif is_premium and heuristic:
                        final_reason = f"{gd_reason} + 启发式:{heuristic}"
                    else:
                        final_reason = gd_reason or heuristic
                    if available:
                        stats["available"] += 1
                        if is_premium:
                            stats["premium"] += 1
                        print(f"  \033[92m✓ 可注册{'(溢价)' if is_premium else ''}\033[0m  {d}  \033[90m[{final_reason[:60]}]\033[0m")
                    else:
                        stats["taken"] += 1
                    results.append(_mk_result(
                        d,
                        "available" if available else "taken",
                        is_premium,
                        final_reason,
                        "godaddypublic",
                        f"premium_ratio={r.get('premium_signal', 0):.2%}, suggestions={r.get('suggestion_count', 0)}",
                    ))

        # 2) .cn 走 CNNIC whois（GoDaddy 公共端点不支持 .cn）
        if cn_suffixes:
            print(f"  [CNNIC whois] 准备查询 .cn 域名...")
            connector = aiohttp.TCPConnector(limit=args.workers * 2, ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                tasks = []
                for base in domain_bases:
                    for s in cn_suffixes:
                        full = f"{base}.{s}"
                        tasks.append(check_domain(session, full, s, semaphore))
                completed = 0
                total_cn = len(tasks)
                for coro in asyncio.as_completed(tasks):
                    result = await coro
                    result["price_usd"] = ""
                    result["currency"] = ""
                    results.append(result)
                    completed += 1
                    if result["status"] == "available":
                        stats["available"] += 1
                        if result["premium"]:
                            stats["premium"] += 1
                    elif result["status"] == "taken":
                        stats["taken"] += 1
                    else:
                        stats["error"] += 1
                    if completed - last_log >= max(1, total_cn // 20) or completed == total_cn:
                        pct = completed / total_cn * 100
                        print(f"\r  [CNNIC] {pct:.0f}% | {completed}/{total_cn} | ✓{stats['available']} ✗{stats['taken']} ?{stats['error']}", end="", flush=True)
                        last_log = completed
                print()
    elif args.source in ("chain", "botoi", "porkbun", "domainr", "whoisfreaks"):
        # ============ Provider 链路模式（多源故障转移） ============
        # 解析 provider 链
        if args.providers:
            chain = [p.strip() for p in args.providers.split(",") if p.strip()]
        else:
            # 默认链
            if args.source == "chain":
                chain = DEFAULT_PROVIDER_CHAIN
            else:
                # 单 provider 模式：就只跑这一个
                chain = [args.source]

        # 显式提示：哪些 provider 因缺 Key 被跳过
        active_providers = []
        for p in chain:
            if p == "porkbun" and (not PORKBUN_KEY or not PORKBUN_SECRET):
                print(f"  ⚠ porkbun: 未配置 PORKBUN_KEY/PORKBUN_SECRET，已跳过")
                continue
            if p == "domainr" and not DOMAINR_RAPIDAPI_KEY:
                continue
            if p == "whoisfreaks" and not WHOISFREAKS_KEY:
                continue
            active_providers.append(p)
        print(f"  [chain] 链路: {' → '.join(active_providers)}")
        print(f"  [chain] 熔断: 连续 {PROVIDER_BREAKER_ERROR_THRESHOLD} 错误 → 冷却 {PROVIDER_BREAKER_ERROR_COOLDOWN}s | 限流 → 冷却 {PROVIDER_BREAKER_RATE_LIMIT_COOLDOWN}s")

        connector = aiohttp.TCPConnector(limit=args.workers * 2, ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = []
            for base in domain_bases:
                for suffix in suffixes:
                    full = f"{base}.{suffix}"
                    tasks.append(check_domain_with_chain(session, full, suffix, semaphore, chain=active_providers))

            completed = 0
            for coro in asyncio.as_completed(tasks):
                result = await coro
                # price_usd 可能是 None，转成空串以便 CSV
                if result.get("price_usd") is None:
                    result["price_usd"] = ""
                results.append(result)
                completed += 1

                if result["status"] == "available":
                    stats["available"] += 1
                    if result["premium"]:
                        stats["premium"] += 1
                    print(f"  \033[92m✓ 可注册{'(溢价)' if result['premium'] else ''}\033[0m  {result['domain']:30s} [{result.get('method', '?')}] {result.get('premiumReason', '')}")
                elif result["status"] == "taken":
                    stats["taken"] += 1
                else:
                    stats["error"] += 1

                # 进度条
                if completed - last_log >= max(1, total // 50) or completed == total:
                    last_log = completed
                    pct = completed / total * 100
                    elapsed = time.time() - start_time
                    speed = completed / elapsed if elapsed > 0 else 0
                    eta = (total - completed) / speed if speed > 0 else 0
                    bar_len = 30
                    filled = int(bar_len * completed / total)
                    bar = "█" * filled + "░" * (bar_len - filled)
                    eta_str = f"{int(eta//60)}m{int(eta%60)}s" if eta > 60 else f"{int(eta)}s"
                    print(f"\r  [{bar}] {pct:.1f}% | {completed}/{total} | {speed:.0f}/s | 剩余:{eta_str} | ✓{stats['available']} ✗{stats['taken']} ?{stats['error']}", end="", flush=True)

            # 打印 provider 健康快照
            print(f"\n\n  [chain] Provider 健康快照：")
            for name in active_providers:
                s = get_provider_status(name)
                cooldown = s.remaining_cooldown()
                status = "✓" if s.is_available() else f"⏸{cooldown:.0f}s"
                print(f"    {name:14s} {status}  ok={s.total_success} fail={s.total_fail} rl={s.total_rate_limit}")
    else:
        # ============ RDAP 多源模式 ============
        connector = aiohttp.TCPConnector(limit=args.workers * 2, ssl=False)
        headers = {
            "User-Agent": "Mozilla/5.0 DomainChecker/3.0",
            "Accept": "application/json",
        }

        async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
            tasks = []
            for base in domain_bases:
                for suffix in suffixes:
                    full = f"{base}.{suffix}"
                    tasks.append(check_domain(session, full, suffix, semaphore))

            completed = 0
            for coro in asyncio.as_completed(tasks):
                result = await coro
                result["price_usd"] = ""
                result["currency"] = ""
                results.append(result)
                completed += 1

                if result["status"] == "available":
                    stats["available"] += 1
                    if result["premium"]:
                        stats["premium"] += 1
                    print(f"  \033[92m✓ 可注册{'(溢价)' if result['premium'] else ''}\033[0m  {result['domain']}")
                elif result["status"] == "taken":
                    stats["taken"] += 1
                else:
                    stats["error"] += 1

                # 进度条
                if completed - last_log >= max(1, total // 100) or completed == total:
                    last_log = completed
                    pct = completed / total * 100
                    elapsed = time.time() - start_time
                    speed = completed / elapsed if elapsed > 0 else 0
                    eta = (total - completed) / speed if speed > 0 else 0
                    bar_len = 30
                    filled = int(bar_len * completed / total)
                    bar = "█" * filled + "░" * (bar_len - filled)
                    eta_str = f"{int(eta//60)}m{int(eta%60)}s" if eta > 60 else f"{int(eta)}s"
                    print(f"\r  [{bar}] {pct:.1f}% | {completed}/{total} | {speed:.0f}/s | 剩余:{eta_str} | ✓{stats['available']} ✗{stats['taken']} ?{stats['error']}", end="", flush=True)

    elapsed_total = time.time() - start_time
    print(f"\n\n{'='*60}")
    print(f"  查询完成！耗时: {elapsed_total:.1f}s")
    print(f"  可注册: \033[92m{stats['available']}\033[0m (其中可能溢价: \033[95m{stats['premium']}\033[0m)")
    print(f"  已注册: \033[91m{stats['taken']}\033[0m")
    print(f"  查询错误: \033[93m{stats['error']}\033[0m")
    print(f"{'='*60}\n")

    # 写出 CSV
    csv_fields = [
        "domain", "suffix", "status", "premium", "premiumReason",
        "price_usd", "currency", "method", "detail", "checked_at"
    ]
    if args.with_confidence:
        csv_fields.append("confidence")

    with open(args.output, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields)
        writer.writeheader()
        for row in sorted(results, key=lambda x: x["domain"]):
            if args.with_confidence:
                row["confidence"] = score_confidence(row)
            writer.writerow({k: row.get(k, "") for k in csv_fields})

    # 可注册域名列表
    available_file = args.output.replace(".csv", "_available.txt")
    available_list = [r for r in results if r["status"] == "available"]
    with open(available_file, "w", encoding="utf-8") as f:
        f.write(f"# 可注册域名列表 - 生成时间: {datetime.now()}\n")
        f.write(f"# 总计: {len(available_list)} 个\n")
        f.write(f"# 溢价: {stats['premium']} 个\n\n")
        for r in sorted(available_list, key=lambda x: x["domain"]):
            tag = " [溢价]" if r["premium"] else ""
            f.write(f"{r['domain']}{tag}\n")

    print(f"  完整结果: {args.output}")
    print(f"  可注册列表: {available_file}\n")


# ================== TLD 预设系统（借鉴 saidutt46/domain-check） ==================
# 常用 TLD 组合，一键选择
TLD_PRESETS = {
    "startup":  ["com", "org", "io", "ai", "tech", "app", "dev", "xyz"],
    "tech":     ["io", "ai", "app", "dev", "tech", "cloud", "software"],
    "creative": ["design", "art", "studio", "media", "photo"],
    "finance":  ["finance", "capital", "fund", "money", "bank"],
    "cn":       ["com", "cn", "com.cn", "net.cn"],
    "all":      None,  # None 表示使用 IANA 全部 1200+ TLD
}


def resolve_tld_preset(preset_name: str) -> list[str] | None:
    """解析 TLD 预设名 → TLD 列表

    Returns:
        list: 具体的 TLD 列表
        None: 全部 TLD（仅当 preset == 'all'）
    """
    if preset_name in TLD_PRESETS:
        return TLD_PRESETS[preset_name]
    return None


# ================== 置信度评分系统（借鉴 sithulaka/DomainChecker） ==================
# 给每个结果一个置信度等级，让用户决定优先级
CONFIDENCE_VERY_HIGH = "VERY_HIGH"   # RDAP 404 / DNS 有记录
CONFIDENCE_HIGH = "HIGH"             # RDAP 200 (已注册)
CONFIDENCE_MEDIUM = "MEDIUM"         # RDAP 失败 + WHOIS 返回可用
CONFIDENCE_LOW = "LOW"               # 所有源都失败，仅 DNS 无记录


def score_confidence(result: dict) -> str:
    """根据结果详情评分

    评分原则（修复 P0-002）：
      - 错误 → LOW
      - DNS 预筛单源（无 RDAP 二次验证）→ taken=LOW, available=LOW（DNS 不可信）
      - DNS 预筛 + RDAP 二次确认 → taken=HIGH, available=HIGH
      - DNS 预筛 + RDAP 404（推翻 DNS 结论）→ available=LOW（DNS 不可信）
      - RDAP 主源 404 → available=VERY_HIGH（权威判定）
      - RDAP 主源 200 → taken=HIGH
      - RDAP 多源 consensus → taken=HIGH, available=VERY_HIGH
      - WHOIS 端口 43 → MEDIUM
      - 商业 API（GoDaddy/Porkbun/Domainr/WhoisFreaks）→ taken=VERY_HIGH, available=HIGH

    Returns:
        "VERY_HIGH" / "HIGH" / "MEDIUM" / "LOW"
    """
    status = result.get("status", "error")
    method = result.get("method", "")
    detail = result.get("detail", "")

    if status == "error":
        # 所有源都失败 → LOW
        return CONFIDENCE_LOW

    # === DNS 预筛路径（修复 P0-001 后）===
    if method.startswith("dns_prefilter"):
        # DNS 预筛 + RDAP 二次确认成功（taken + RDAP 200）
        if "+rdap_verify" in method:
            if status == "taken":
                return CONFIDENCE_HIGH
            return CONFIDENCE_HIGH
        # DNS 预筛命中但 RDAP 验证失败（保留 taken 降级）
        if "unverified" in method:
            if status == "taken":
                return CONFIDENCE_LOW  # 未经验证，不可信
            return CONFIDENCE_LOW
        # DNS 预筛 + RDAP 404（DNS 劫持推翻）
        if "dns_hijack" in method:
            if status == "available":
                return CONFIDENCE_LOW  # DNS 不可信，但 RDAP 404 = 权威
            return CONFIDENCE_LOW
        # 旧版单源 DNS 预筛（没经过修复的代码路径）
        if status == "available":
            return CONFIDENCE_LOW  # DNS 显示无记录 = RDAP 还要走
        elif status == "taken":
            return CONFIDENCE_LOW  # 没二次验证，不可信

    # === RDAP 路径 ===
    if method == "rdap" or method.startswith("consensus"):
        if status == "available":
            return CONFIDENCE_VERY_HIGH  # RDAP 404 = 权威
        elif status == "taken":
            return CONFIDENCE_HIGH

    # === RDAP 主源 404/200 单一源判定（method="primary"） ===
    if method == "primary":
        if status == "available":
            return CONFIDENCE_VERY_HIGH  # RDAP 404 = 权威
        elif status == "taken":
            return CONFIDENCE_HIGH

    # === 商业 API 路径（GoDaddy/Porkbun/Domainr/WhoisFreaks） ===
    if method in ("godaddy", "godaddypublic", "porkbun", "whoisfreaks", "domainr"):
        if status == "available":
            return CONFIDENCE_HIGH  # 商业 API 可信
        elif status == "taken":
            return CONFIDENCE_VERY_HIGH  # 商业 API 权威

    # === WHOIS 端口 43 ===
    if method == "whois" or method.startswith("whois_"):
        return CONFIDENCE_MEDIUM

    return CONFIDENCE_MEDIUM


# ================== 负缓存：no_rdap TLD 集合（借鉴 saidutt46） ==================
# 记录哪些 TLD 没有 RDAP 服务，避免反复尝试失败
NO_RDAP_TLDS = set([
    # 已知没有 RDAP 端点的 ccTLD
    "cn",  # 改用 whois 43
    "com.cn", "net.cn", "org.cn", "edu.cn", "gov.cn",
    "xn--fiqs8s",  # .中国
])


def is_no_rdap(tld: str) -> bool:
    """判断 TLD 是否无 RDAP 服务（应直接走 whois 43 或其他路径）"""
    return tld.lower() in NO_RDAP_TLDS


# ================== WHOIS 服务器动态发现（借鉴 saidutt46/domain-check） ==================
# 通过 whois.iana.org 查询指定 TLD 的权威 WHOIS 服务器
# 三层缓存：硬编码 → 内存 → IANA 远程发现

# 已知 whois 服务器（硬编码缓存）
KNOWN_WHOIS_SERVERS = {
    "cn": "whois.cnnic.cn",
    "com.cn": "whois.cnnic.cn",
    "net.cn": "whois.cnnic.cn",
    "org.cn": "whois.cnnic.cn",
    "edu.cn": "whois.cnnic.cn",
    "gov.cn": "whois.cnnic.cn",
    "xn--fiqs8s": "whois.cnnic.cn",  # .中国
    "com": "whois.verisign-grs.com",
    "net": "whois.verisign-grs.com",
    "org": "whois.pir.org",
    "io": "whois.nic.io",
    "ai": "whois.nic.ai",
    "app": "whois.nic.google",
    "dev": "whois.nic.google",
    "co": "whois.nic.co",
    "me": "whois.nic.me",
    "tv": "whois.nic.tv",
    "cc": "ccwhois.verisign-grs.com",
    "biz": "whois.biz",
    "info": "whois.afilias.net",
    "us": "whois.nic.us",
    "uk": "whois.nic.uk",
}

# 进程级内存缓存（避免同一 TLD 反复发现）
_whois_cache: dict[str, str] = {}


def discover_whois_server(tld: str) -> str | None:
    """通过 IANA 查询指定 TLD 的权威 WHOIS 服务器

    优先级：硬编码 → 内存缓存 → IANA 远程发现

    Returns:
        whois 服务器主机名（如 "whois.verisign-grs.com"），失败返回 None
    """
    tld_clean = tld.lower().lstrip(".")
    if not tld_clean:
        return None

    # 1) 硬编码
    if tld_clean in KNOWN_WHOIS_SERVERS:
        return KNOWN_WHOIS_SERVERS[tld_clean]

    # 2) 内存缓存
    if tld_clean in _whois_cache:
        return _whois_cache[tld_clean]

    # 3) IANA 远程发现
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(8)
        s.connect(("whois.iana.org", 43))
        s.sendall(f"{tld_clean}\r\n".encode("utf-8"))
        chunks = []
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
        s.close()
        text = b"".join(chunks).decode("utf-8", errors="ignore")
        for line in text.splitlines():
            if line.strip().lower().startswith("whois:"):
                server = line.split(":", 1)[1].strip()
                if server:
                    _whois_cache[tld_clean] = server
                    return server
    except Exception as e:
        return None

    return None


# ================== 模块导入时自动加载 IANA Bootstrap ==================
# 解决 domain_server.py 之前不加载 bootstrap 导致 RDAP_SOURCES 只有 8 个硬编码的问题
_bootstrap_init_state = {
    "loaded": False,
    "loading": False,
    "count": 0,
    "elapsed_sec": 0.0,
    "error": "",
}


def init_bootstrap(timeout_sec: float = 8.0) -> bool:
    """同步加载 IANA bootstrap，更新 RDAP_SOURCES（阻塞，最多 8s）

    适用：CLI 启动时 / Server 启动时，确保 TLD 字典就绪再接收请求。
    """
    if _bootstrap_init_state["loading"]:
        return False
    _bootstrap_init_state["loading"] = True
    t0 = time.time()
    try:
        data = load_iana_bootstrap_sync()
        elapsed = time.time() - t0
        if data:
            new_sources = build_rdap_sources(data)
            RDAP_SOURCES.clear()
            RDAP_SOURCES.update(new_sources)
            _bootstrap_init_state["loaded"] = True
            _bootstrap_init_state["count"] = len(RDAP_SOURCES)
            _bootstrap_init_state["elapsed_sec"] = elapsed
            print(f"[IANA] 已加载 {len(data)} 个 TLD → RDAP_SOURCES 共 {len(RDAP_SOURCES)} 个 TLD ({elapsed:.1f}s)")
            return True
        _bootstrap_init_state["error"] = "no_data"
        return False
    except Exception as e:
        elapsed = time.time() - t0
        _bootstrap_init_state["error"] = f"{type(e).__name__}: {str(e)[:80]}"
        print(f"[IANA] 加载失败（不影响现有 8 个 TLD）: {_bootstrap_init_state['error']} ({elapsed:.1f}s)")
        return False
    finally:
        _bootstrap_init_state["loading"] = False


def init_bootstrap_async() -> threading.Thread:
    """后台线程加载 IANA bootstrap，不阻塞 import / 启动

    适用：模块 import 末尾自动调用，server 启动后立即可服务。
    """
    t = threading.Thread(target=lambda: init_bootstrap(timeout_sec=10.0), daemon=True, name="iana-bootstrap-init")
    t.start()
    return t


# 模块导入时立即启动后台加载（兼容 CLI 和 server）
_init_bootstrap_thread = init_bootstrap_async()


def get_bootstrap_status() -> dict:
    """供 server /api/check 返回诊断信息"""
    return dict(_bootstrap_init_state)


def main():
    p = argparse.ArgumentParser(description="域名批量查询 - 通用版")
    p.add_argument("--prefix", default="ai", help="域名前缀（默认 ai，留空用 --prefix ''）")
    p.add_argument("--custom-suffix", default="", help="域名主体后缀（如 am），默认空")
    p.add_argument("--length", type=int, default=5, help="域名主体总长度（默认 5）")
    p.add_argument("--suffix", nargs="+", default=["com", "cn"], help="顶级后缀（默认 com cn，可用: com net org cn io ai app dev）")
    p.add_argument("--letters", default="all", help="中间字母范围：all / a-m / n-z / a-h 等")
    p.add_argument("--workers", type=int, default=30, help="并发数（默认 30，建议 30-50）")
    p.add_argument("--output", default="domain_results.csv", help="输出 CSV 文件名")
    p.add_argument("--no-dns-prefilter", action="store_true",
                   help="关闭 DNS 预筛（默认开启，可拦截 80%% 已注册域名）")
    p.add_argument("--refresh-iana", action="store_true",
                   help="强制刷新 IANA bootstrap 缓存")
    p.add_argument("--source",
                   choices=["rdap", "godaddy", "godaddypublic", "auto", "chain",
                            "botoi", "porkbun", "domainr", "whoisfreaks"],
                   default="rdap",
                   help="数据源: rdap / godaddy / godaddypublic / auto / chain(多源故障转移) "
                        "/ botoi (免Key) / porkbun (需Key, real price) "
                        "/ domainr (RapidAPI, 10k free/month) / whoisfreaks (500 free credits)")
    p.add_argument("--providers", default="",
                   help="自定义 provider 链路（--source chain 时生效）"
                        " 例如: rdap,godaddypublic,botoi,porkbun")
    p.add_argument("--tld-preset", choices=list(TLD_PRESETS.keys()) + ["none"], default="none",
                   help="TLD 预设: startup / tech / creative / finance / cn / all / none")
    p.add_argument("--with-confidence", action="store_true",
                   help="在结果中附加 confidence 字段（VERY_HIGH / HIGH / MEDIUM / LOW）")
    args = p.parse_args()

    # 处理 TLD 预设
    if args.tld_preset != "none":
        tld_list = resolve_tld_preset(args.tld_preset)
        if tld_list is None:
            print(f"  [preset] '{args.tld_preset}' = 全部 1200+ TLD（将使用 IANA 完整列表）")
            args.suffix = ["com"]  # 占位，实际会扩展
        else:
            print(f"  [preset] '{args.tld_preset}' = {tld_list}")
            args.suffix = tld_list

    try:
        asyncio.run(run_checker(args))
    except KeyboardInterrupt:
        print("\n\n已中断")
        sys.exit(1)


if __name__ == "__main__":
    main()
