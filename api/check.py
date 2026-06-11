"""
域名可用性查询 API (Vercel Serverless Function)

架构：
  - 启动时从 IANA 官方 bootstrap 加载 1200+ TLD 的 RDAP 服务器
  - 优先使用主源 RDAP，失败时回退到 IANA 中继
  - 并发查询所有域名
  - 附加 confidence 字段

仅使用 Python 内置模块，无需任何第三方依赖。
Vercel 限制：函数执行时间 ≤ 10s（Hobby），故 MAX_DOMAINS=20、QUERY_TIMEOUT=4s
"""

from http.server import BaseHTTPRequestHandler
import json
import socket
import ssl
import urllib.request
import urllib.error
import concurrent.futures
import os
import tempfile
import time
from dataclasses import dataclass, asdict, field
from typing import Optional, Literal

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

MAX_DOMAINS_PER_REQUEST = 20   # Vercel 10s 限制下，单请求最多 20 个域名
QUERY_TIMEOUT = 4              # 每次 RDAP 查询超时（秒）
MAX_WORKERS = 8                # 并发线程数（避免触发 Vercel CPU 限制）
CNNIC_WHOIS_CONFIRM_ATTEMPTS = 2

# ---------------------------------------------------------------------------
# 启动时加载 IANA Bootstrap（1200+ TLD 的 RDAP 服务器表）
# ---------------------------------------------------------------------------

_bootstrap_cache: dict[str, list[str]] = {}
_bootstrap_loaded_at: float = 0
_bootstrap_lock_until: float = 0  # 失败后冷却时间

IANA_BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"
CACHE_FILE = os.path.join(tempfile.gettempdir(), "iana_rdap_bootstrap.json")
CACHE_TTL = 7 * 24 * 3600  # 7 天


def _parse_iana_bootstrap(data: dict) -> dict[str, list[str]]:
    """解析 IANA bootstrap JSON：services 字段是 [tlds, urls] 列表

    重要：RDAP URL 通常以 / 结尾，但需要去掉尾随的 path 段（如 /v1/），
    然后附加 /domain/<name>，但实际 PIR 端点要求路径是 /rdap/domain/<name>。

    处理策略：保留 IANA 给出的完整 URL 路径，直接追加 domain/<name>。
    例如 IANA 给 https://rdap.publicinterestregistry.org/rdap/
         → 完整 URL: https://rdap.publicinterestregistry.org/rdap/domain/<name>
    """
    result: dict[str, list[str]] = {}
    for entry in data.get("services", []):
        tld_list, urls = entry
        if not urls:
            continue
        base = urls[0].rstrip("/")
        for tld in tld_list:
            tld_lower = tld.lower()
            url = base + "/domain/"
            result.setdefault(tld_lower, []).append(url)
    return result


def load_iana_bootstrap() -> dict[str, list[str]]:
    """加载 IANA bootstrap（带磁盘缓存、内存缓存、冷却时间）"""
    global _bootstrap_cache, _bootstrap_loaded_at, _bootstrap_lock_until
    now = time.time()

    # 1) 内存缓存
    if _bootstrap_cache and now - _bootstrap_loaded_at < CACHE_TTL:
        return _bootstrap_cache

    # 2) 冷却中（最近失败过）→ 用空表兜底
    if now < _bootstrap_lock_until:
        return _bootstrap_cache

    # 3) 磁盘缓存
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if now - cached.get("_cached_at", 0) < CACHE_TTL:
                _bootstrap_cache = _parse_iana_bootstrap(cached["data"])
                _bootstrap_loaded_at = now
                if _bootstrap_cache:
                    return _bootstrap_cache
        except Exception:
            pass

    # 4) 远程获取
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(
            IANA_BOOTSTRAP_URL,
            headers={"User-Agent": "DomainChecker-Vercel/1.0"},
        )
        with urllib.request.urlopen(req, timeout=6, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        _bootstrap_cache = _parse_iana_bootstrap(data)
        _bootstrap_loaded_at = now
        # 写磁盘
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({"_cached_at": now, "data": data}, f)
        except Exception:
            pass
        return _bootstrap_cache
    except Exception:
        # 失败 → 5 分钟冷却
        _bootstrap_lock_until = now + 300
        return _bootstrap_cache


# 启动时立即加载（cold start）
_bootstrap_cache = load_iana_bootstrap()


# 兜底：常见 TLD 的 RDAP 源（bootstrap 加载失败时用）
FALLBACK_RDAP_SOURCES: dict[str, list[str]] = {
    "com":       ["https://rdap.verisign.com/com/v1/domain/", "https://rdap.iana.org/domain/"],
    "net":       ["https://rdap.verisign.com/net/v1/domain/", "https://rdap.iana.org/domain/"],
    "org":       ["https://rdap.publicinterestregistry.org/rdap/domain/", "https://rdap.iana.org/domain/"],
    "ai":        ["https://rdap.nic.ai/domain/", "https://rdap.iana.org/domain/"],
    "app":       ["https://rdap.nic.google/domain/", "https://rdap.iana.org/domain/"],
    "dev":       ["https://rdap.nic.google/domain/", "https://rdap.iana.org/domain/"],
    "io":        ["https://rdap.identitydigital.services/rdap/domain/", "https://rdap.nic.io/domain/", "https://rdap.iana.org/domain/"],
    "xyz":       ["https://rdap.centralnic.com/xyz/domain/", "https://rdap.iana.org/domain/"],
    "cloud":     ["https://rdap.registry.cloud/rdap/domain/", "https://rdap.iana.org/domain/"],
    "tech":      ["https://rdap.iana.org/domain/"],
    "software":  ["https://rdap.iana.org/domain/"],
    # .cn 走 whois 端口 43
    "cn":        ["whois://whois.cnnic.cn:43"],
    "com.cn":    ["whois://whois.cnnic.cn:43"],
    "net.cn":    ["whois://whois.cnnic.cn:43"],
    "org.cn":    ["whois://whois.cnnic.cn:43"],
}


def get_rdap_sources(suffix: str) -> list[str]:
    """获取 TLD 的 RDAP 源列表（bootstrap 优先，fallback 兜底）"""
    suffix = suffix.lower()
    sources = _bootstrap_cache.get(suffix) or FALLBACK_RDAP_SOURCES.get(suffix)
    if not sources:
        # 未知 TLD：直接用 IANA 中继（能 cover 绝大多数 gTLD）
        sources = ["https://rdap.iana.org/domain/"]
    return sources


# ---------------------------------------------------------------------------
# RDAP / Whois 查询
# ---------------------------------------------------------------------------

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


def query_rdap(domain: str, rdap_url: str) -> int:
    """RDAP 查询

    Returns:
        200 — 已注册
        404 — 可注册
        -1  — 查询失败
    """
    url = rdap_url + domain
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/rdap+json",
            "User-Agent": "Mozilla/5.0 (compatible; DomainChecker/1.0)",
        },
    )
    try:
        resp = urllib.request.urlopen(req, timeout=QUERY_TIMEOUT, context=_ssl_ctx)
        resp.read()
        resp.close()
        return 200
    except urllib.error.HTTPError as e:
        return 404 if e.code == 404 else -1
    except Exception:
        return -1


def query_whois_once(domain: str, host: str = "whois.cnnic.cn", port: int = 43) -> int:
    """单次 Whois 端口 43 查询。200=taken, 404=available, -1=无明确结论。"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(QUERY_TIMEOUT)
        sock.connect((host, port))
        sock.sendall((domain + "\r\n").encode("ascii"))
        chunks = []
        while True:
            data = sock.recv(4096)
            if not data:
                break
            chunks.append(data)
        sock.close()
        response = b"".join(chunks).decode("utf-8", errors="replace").lower()
        if not response.strip():
            return -1
        if "no matching" in response or "not found" in response:
            return 404
        if "domain name:" in response or "registrant:" in response:
            return 200
        return -1
    except Exception:
        return -1


def query_whois(domain: str, host: str = "whois.cnnic.cn", port: int = 43) -> int:
    """Whois 查询。

    CNNIC 严格模式：已注册一次命中即可；可注册必须连续两次明确 No matching。
    这样宁可返回需复核，也不把空响应/偶发错误误判成可注册。
    """
    if "cnnic.cn" not in host.lower():
        return query_whois_once(domain, host, port)

    available_hits = 0
    for idx in range(CNNIC_WHOIS_CONFIRM_ATTEMPTS):
        code = query_whois_once(domain, host, port)
        if code == 200:
            return 200
        if code == 404:
            available_hits += 1
        if idx < CNNIC_WHOIS_CONFIRM_ATTEMPTS - 1:
            time.sleep(0.15)

    if available_hits >= CNNIC_WHOIS_CONFIRM_ATTEMPTS:
        return 404
    return -1


def query_single_source(domain: str, source_url: str) -> int:
    """根据数据源 URL 自动选择 RDAP 或 Whois 协议"""
    if source_url.startswith("whois://"):
        addr = source_url[len("whois://"):]
        if ":" in addr:
            host, port_str = addr.rsplit(":", 1)
            port = int(port_str)
        else:
            host, port = addr, 43
        return query_whois(domain, host, port)
    return query_rdap(domain, source_url)


# ---------------------------------------------------------------------------
# Premium 启发式判断（短域名、英文词根、回文、顺序字母等）
# ---------------------------------------------------------------------------

COMMON_WORDS = {
    "box", "car", "dog", "cat", "man", "boy", "kid", "sun", "sky", "sea",
    "map", "app", "web", "net", "biz", "pro", "top", "vip", "fun", "run",
    "win", "lab", "job", "pay", "buy", "fit", "joy", "key", "law", "log",
    "mix", "tax", "tea", "way", "zoo", "art", "bar", "bed", "big", "bit",
    "bus", "can", "cap", "cut", "day", "die", "dry", "eat", "egg", "eye",
    "fan", "far", "fat", "fee", "fly", "gas", "get", "god", "gun", "guy",
    "hit", "hot", "ice", "let", "lie", "lot", "low", "mad", "new", "now",
    "off", "oil", "old", "one", "out", "own", "pen", "pie", "pop", "put",
    "red", "rid", "row", "sad", "say", "set", "she", "sit", "six", "son",
    "ten", "try", "use", "van", "war", "wet", "who", "why", "yes", "yet",
    "you", "ace", "act", "add", "age", "ago", "aid", "aim", "air", "all",
    "any", "arm", "ask", "bad", "bag", "ban", "bat", "bay", "big", "bio",
    "bug", "cab", "cow", "cry", "cup", "hub", "ink", "inn", "jam", "jaw",
    "jet", "led", "nap", "nod", "nut", "oak", "owl", "pad", "pan", "pat",
    "pea", "peg", "pep", "per", "pet", "pie", "pig", "pin", "pit", "pod",
    "pot", "pub", "rag", "ram", "ran", "rat", "raw", "ray", "red", "ref",
    "rib", "rid", "rig", "rim", "rip", "rob", "rod", "rot", "rub", "rug",
    "rum", "rut", "sac", "sad", "sap", "sat", "saw", "sec", "set", "shy",
    "sin", "sir", "ski", "sob", "sod", "soy", "spy", "sub", "sue", "sum",
    "tab", "tad", "tag", "tan", "tap", "tar", "tee", "ten", "tie", "tin",
    "tip", "toe", "tom", "ton", "too", "tot", "tow", "toy", "tub", "tug",
    "two", "urn", "vat", "vet", "via", "vie", "vim", "vow", "wad", "wag",
    "wan", "war", "was", "wax", "wed", "wet", "wig", "wit", "wok", "won",
    "woo", "yak", "yam", "yap", "yaw", "yay", "yea", "yen", "yep", "zip",
    "bird", "baby", "shop", "card", "code", "data", "face", "file", "game",
    "home", "link", "mail", "news", "note", "play", "rate", "star", "tech",
    "test", "type", "view", "vote", "book", "chat", "city", "club", "cool",
    "deal", "door", "edge", "feed", "film", "fire", "fish", "food", "gift",
    "girl", "goal", "gold", "good", "grow", "hair", "hand", "hard", "head",
    "help", "hero", "high", "hill", "hold", "host", "idea", "iron", "jack",
    "join", "jump", "keep", "king", "know", "land", "late", "lead", "life",
    "like", "line", "list", "live", "load", "lock", "long", "look", "lord",
    "lose", "love", "luck", "made", "make", "male", "mark", "meal", "meat",
    "meet", "mind", "miss", "mode", "moon", "more", "most", "move", "much",
    "must", "name", "near", "neat", "need", "nice", "nine", "none", "nose",
    "okay", "once", "only", "open", "over", "pack", "page", "paid", "pain",
    "pair", "palm", "park", "part", "pass", "past", "path", "peak", "pick",
    "pine", "pink", "pipe", "plan", "plot", "plug", "plus", "poem", "poet",
    "poll", "pool", "poor", "port", "post", "pour", "pull", "pump", "pure",
    "push", "race", "rain", "rank", "rare", "read", "real", "rent", "rest",
    "rich", "ride", "ring", "rise", "risk", "road", "rock", "role", "roll",
    "roof", "room", "root", "rope", "rose", "rule", "rush", "safe", "said",
    "sake", "sale", "salt", "same", "sand", "save", "seal", "seat", "seed",
    "seek", "seem", "self", "sell", "send", "ship", "shut", "side", "sign",
    "site", "size", "skin", "slip", "slow", "snow", "soft", "soil", "sold",
    "sole", "some", "song", "soon", "sort", "soul", "spot", "stay", "stem",
    "step", "stop", "such", "suit", "sure", "swim", "tail", "take", "tale",
    "talk", "tall", "tank", "tape", "task", "taxi", "team", "tell", "tend",
    "tent", "term", "text", "than", "that", "them", "then", "they", "thin",
    "this", "thus", "tide", "till", "time", "tiny", "tire", "told", "tone",
    "took", "tool", "tops", "tour", "town", "trap", "tree", "trim", "trio",
    "trip", "true", "tube", "tune", "turn", "twin", "type", "ugly", "undo",
    "unit", "upon", "used", "user", "vary", "vast", "verb", "very", "vest",
    "vice", "wade", "wage", "wait", "wake", "walk", "wall", "want", "ward",
    "warm", "warn", "wash", "wave", "ways", "weak", "wear", "week", "well",
    "went", "west", "what", "when", "wide", "wife", "wild", "will", "wind",
    "wine", "wing", "wire", "wise", "wish", "with", "wood", "word", "work",
    "worm", "worn", "wrap", "yard", "year", "yell", "zero", "zone",
    "travel", "stream", "studio", "store", "strong", "switch", "system",
    "target", "thanks", "ticket", "toward", "triple", "tunnel", "twelve",
    "twenty", "typing", "unique", "united", "update", "useful", "valley",
    "vision", "volume", "wealth", "weapon", "weekly", "weight", "window",
    "winner", "winter", "within", "wonder", "worker",
    "api", "ml", "bot", "dev", "hub",
    # 常见 4 字母品牌/科技/语义词，避免误报
    "java", "ruby", "rust", "perl", "node", "ajax", "json", "llvm", "mips",
    "arm", "java", "ruby", "rust", "perl", "php", "html", "css", "sql",
    "blog", "shop", "loan", "bank", "cash", "gift", "sale", "deal", "fans",
    "cars", "bike", "boat", "shoe", "wine", "golf", "yoga", "pizza",
    "baby", "kids", "love", "best", "real", "true", "safe", "plus", "easy",
    "live", "play", "open", "free", "rich", "fast", "cool", "warm", "fine",
    "vip", "pro", "max", "top", "hot", "new", "big", "net", "lab", "hub",
}
VOWELS = set("aeiou")
CONSONANTS = set("bcdfghjklmnpqrstvwxyz")


def get_premium_reason(domain: str) -> str | None:
    """启发式溢价检测（基于模式，非真实价格）"""
    base = domain.split(".", 1)[0].lower()
    if not base:
        return None
    is_alpha = base.isalpha()
    is_same = is_alpha and len(set(base)) == 1
    is_palindrome = is_alpha and base == base[::-1] and len(base) >= 2
    is_ascending = is_alpha and all(ord(base[i]) + 1 == ord(base[i+1]) for i in range(len(base)-1))
    is_descending = is_alpha and all(ord(base[i]) - 1 == ord(base[i+1]) for i in range(len(base)-1))
    is_all_vowels = is_alpha and all(c in VOWELS for c in base)
    is_all_consonants = is_alpha and all(c in CONSONANTS for c in base)
    L = len(base)

    if L <= 3:
        if base in COMMON_WORDS: return "短单词"
        if is_same: return "全相同字母"
        if is_palindrome: return "回文短域名"
        return "短域名"

    if L == 4:
        if base in COMMON_WORDS: return "英文单词"
        if is_same: return "全相同字母"
        if is_palindrome: return "回文"
        if base[0] == base[2] and base[1] == base[3] and base[0] != base[1]: return "abab重复"
        if base[0] == base[1] and base[2] == base[3] and base[0] != base[2]: return "aabb模式"
        if is_ascending: return "顺序字母"
        if is_descending: return "倒序字母"
        if is_all_vowels: return "全元音"
        # 全辅音在 4 字符中通常只是随机组合，不应自动标溢价
        # （如 qzqx、zxcv 都不是溢价信号）
        return None

    if L == 5:
        if base in COMMON_WORDS: return "英文单词"
        for p in (1, 2):
            if base[p:] in COMMON_WORDS: return f"词根({base[p:]})"
        if base[-3:] in COMMON_WORDS: return f"词尾词({base[-3:]})"
        if base[:3] in COMMON_WORDS: return f"词头词({base[:3]})"
        if is_same: return "全相同字母"
        if is_palindrome: return "回文"
        return None

    if L == 6:
        if base in COMMON_WORDS: return "英文单词"
        if base[:3] == base[3:]: return "abcabc重复"
        if is_palindrome: return "回文"
        if base[-3:] in COMMON_WORDS: return f"词尾词({base[-3:]})"
        if base[:3] in COMMON_WORDS: return f"词头词({base[:3]})"
        for s in range(1, L-2):
            for e in range(s+3, min(s+5, L+1)):
                if base[s:e] in COMMON_WORDS: return f"含词({base[s:e]})"
        return None

    if L <= 8:
        if base in COMMON_WORDS: return "英文单词"
        for p in (1, 2):
            if base[p:] in COMMON_WORDS: return f"词根({base[p:]})"
            if base[:L-p] in COMMON_WORDS: return f"词根({base[:L-p]})"
        if base[-3:] in COMMON_WORDS: return f"词尾词({base[-3:]})"
        if base[:3] in COMMON_WORDS: return f"词头词({base[:3]})"
        return None

    return None


# ---------------------------------------------------------------------------
# 置信度评分
# ---------------------------------------------------------------------------

CONF_VERY_HIGH = "VERY_HIGH"
CONF_HIGH = "HIGH"
CONF_MEDIUM = "MEDIUM"
CONF_LOW = "LOW"


# ---------------------------------------------------------------------------
# 三态结果 + CheckResult dataclass
# ---------------------------------------------------------------------------

# Status: 三态明确分开 - taken(已注册) / available(可注册) / error(查询失败)
# Method: primary(主源一次成功) / fallback(主源失败后回退成功) / all_failed(全部失败)
# Confidence: VERY_HIGH / HIGH / MEDIUM / LOW
StatusType = Literal["taken", "available", "error"]
MethodType = Literal["primary", "fallback", "all_failed"]
ConfidenceType = Literal["VERY_HIGH", "HIGH", "MEDIUM", "LOW"]


@dataclass
class CheckResult:
    """单个域名查询结果"""
    domain: str
    suffix: str
    status: StatusType
    method: MethodType
    detail: str = ""
    premium: bool = False
    premiumReason: Optional[str] = None
    confidence: ConfidenceType = CONF_LOW
    sources_ok: int = 0
    sources_total: int = 0
    verification_rounds: int = 0
    verification_detail: str = ""
    is_whois: bool = False
    checked_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


def make_result(domain: str, suffix: str, status: StatusType, method: MethodType,
                detail: str = "", premium_reason: Optional[str] = None,
                sources_ok: int = 0, sources_total: int = 0,
                verification_rounds: int = 0, verification_detail: str = "",
                is_whois: bool = False,
                confidence: Optional[ConfidenceType] = None) -> CheckResult:
    """统一构造 CheckResult，自动根据 status/sources 推断 confidence"""
    if confidence is None:
        confidence = score_confidence(status, sources_ok, sources_total, is_whois)
    return CheckResult(
        domain=domain,
        suffix=suffix,
        status=status,
        method=method,
        detail=detail,
        premium=premium_reason is not None,
        premiumReason=premium_reason,
        confidence=confidence,
        sources_ok=sources_ok,
        sources_total=sources_total,
        verification_rounds=verification_rounds or sources_total or 1,
        verification_detail=verification_detail or detail,
        is_whois=is_whois,
    )


def score_confidence(status: str, sources_ok: int, sources_total: int, is_whois: bool) -> str:
    """根据查询方法和数据源情况计算置信度"""
    if status == "error":
        return CONF_LOW
    if status == "taken":
        if is_whois:
            return CONF_HIGH
        if sources_ok >= 2:
            return CONF_VERY_HIGH
        return CONF_HIGH
    if status == "available":
        if sources_ok >= 2:
            return CONF_VERY_HIGH
        if is_whois:
            return CONF_MEDIUM
        return CONF_HIGH
    return CONF_LOW


# ---------------------------------------------------------------------------
# 核心查询逻辑
# ---------------------------------------------------------------------------

def split_domain_suffix(domain: str) -> tuple[str, str]:
    """拆分域名主体和后缀，优先识别 .com.cn 这类二级公共后缀。"""
    labels = domain.rsplit(".", 2)
    if len(labels) == 3:
        base2, second, tld = labels
        suffix2 = f"{second}.{tld}"
        if suffix2 in FALLBACK_RDAP_SOURCES or suffix2 in _bootstrap_cache:
            return base2, suffix2
    parts = domain.rsplit(".", 1)
    if len(parts) != 2:
        return domain, ""
    return parts[0], parts[1]


def summarize_source_results(results: list[tuple[str, int]]) -> str:
    labels = []
    for idx, (_, code) in enumerate(results, 1):
        if code == 200:
            status = "taken"
        elif code == 404:
            status = "available"
        else:
            status = "failed"
        labels.append(f"第{idx}轮:{status}")
    return "；".join(labels)

def check_domain(domain: str) -> CheckResult:
    """查询单个域名：先主源、失败时并发回退"""
    domain = domain.strip().lower()
    base, suffix = split_domain_suffix(domain)
    if not base or not suffix:
        return make_result(domain, "", "error", "all_failed", "域名格式无效")

    sources = get_rdap_sources(suffix)
    premium_reason = get_premium_reason(domain)
    is_whois = bool(sources and sources[0].startswith("whois://"))
    source_results: list[tuple[str, int]] = []

    # 步骤 1：主源
    primary = query_single_source(domain, sources[0])
    source_results.append((sources[0], primary))
    if primary == 404:
        if len(sources) <= 1 or is_whois:
            rounds = CNNIC_WHOIS_CONFIRM_ATTEMPTS if is_whois else 1
            return make_result(domain, suffix, "available", "primary",
                               premium_reason=premium_reason, sources_ok=1,
                               sources_total=len(sources), verification_rounds=rounds,
                               verification_detail=summarize_source_results(source_results),
                               is_whois=is_whois)
        # 有多个权威源时，available 必须继续复核；taken 冲突优先。
        fallback_results = []
        for src in sources[1:]:
            code = query_single_source(domain, src)
            source_results.append((src, code))
            fallback_results.append(code)
            if code == 200:
                return make_result(domain, suffix, "taken", "fallback",
                                   detail="权威源冲突：已注册信号优先",
                                   premium_reason=premium_reason, sources_ok=1,
                                   sources_total=len(sources),
                                   verification_rounds=len(source_results),
                                   verification_detail=summarize_source_results(source_results),
                                   is_whois=is_whois)
        available_hits = 1 + sum(1 for code in fallback_results if code == 404)
        if available_hits >= 2:
            return make_result(domain, suffix, "available", "fallback",
                               premium_reason=premium_reason, sources_ok=available_hits,
                               sources_total=len(sources),
                               verification_rounds=len(source_results),
                               verification_detail=summarize_source_results(source_results),
                               is_whois=is_whois)
        return make_result(domain, suffix, "error", "all_failed",
                           f"可注册未完成复核：{available_hits}/{len(sources)} 个明确可注册信号",
                           premium_reason=premium_reason, sources_ok=available_hits,
                           sources_total=len(sources),
                           verification_rounds=len(source_results),
                           verification_detail=summarize_source_results(source_results),
                           is_whois=is_whois, confidence=CONF_LOW)
    if primary == 200:
        return make_result(domain, suffix, "taken", "primary",
                           premium_reason=premium_reason, sources_ok=1,
                           sources_total=len(sources), verification_rounds=1,
                           verification_detail=summarize_source_results(source_results),
                           is_whois=is_whois)

    # 步骤 2：主源失败 → 并发所有回退源
    if len(sources) <= 1:
        return make_result(domain, suffix, "error", "all_failed", "主源失败且无回退",
                           premium_reason=premium_reason, sources_ok=0,
                           sources_total=1, verification_rounds=1,
                           verification_detail=summarize_source_results(source_results),
                           is_whois=is_whois, confidence=CONF_LOW)

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(sources))) as pool:
            future_map = {pool.submit(query_single_source, domain, src): src for src in sources[1:]}
            available_hits = 0
            for future in concurrent.futures.as_completed(future_map):
                src = future_map[future]
                try:
                    code = future.result()
                except Exception:
                    source_results.append((src, -1))
                    continue
                source_results.append((src, code))
                if code == 200:
                    return make_result(domain, suffix, "taken", "fallback",
                                       premium_reason=premium_reason, sources_ok=1,
                                       sources_total=len(sources),
                                       verification_rounds=len(source_results),
                                       verification_detail=summarize_source_results(source_results),
                                       is_whois=is_whois)
                if code == 404:
                    available_hits += 1
            if available_hits >= 2:
                return make_result(domain, suffix, "available", "fallback",
                                   premium_reason=premium_reason, sources_ok=available_hits,
                                   sources_total=len(sources),
                                   verification_rounds=len(source_results),
                                   verification_detail=summarize_source_results(source_results),
                                   is_whois=is_whois)
    except Exception:
        pass

    available_seen = sum(1 for _, code in source_results if code == 404)
    detail = (
        f"可注册未完成复核：{available_seen}/{len(sources)} 个明确可注册信号"
        if available_seen
        else "所有数据源查询失败"
    )
    return make_result(domain, suffix, "error", "all_failed", detail,
                       premium_reason=premium_reason,
                       sources_ok=sum(1 for _, code in source_results if code in (200, 404)),
                       sources_total=len(sources),
                       verification_rounds=len(source_results),
                       verification_detail=summarize_source_results(source_results),
                       is_whois=is_whois, confidence=CONF_LOW)


# ---------------------------------------------------------------------------
# Vercel Serverless Handler
# ---------------------------------------------------------------------------

class handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length == 0:
                self._send_json(400, {"error": "请求体为空"})
                return
            raw = self.rfile.read(content_length)
            try:
                body = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                self._send_json(400, {"error": "JSON 解析失败"})
                return

            domains = body.get("domains")
            if not isinstance(domains, list) or len(domains) == 0:
                self._send_json(400, {"error": "缺少 domains 数组"})
                return
            if len(domains) > MAX_DOMAINS_PER_REQUEST:
                self._send_json(400, {"error": f"单次最多 {MAX_DOMAINS_PER_REQUEST} 个域名"})
                return

            # 并发查询
            results: list[dict] = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                future_map = {pool.submit(check_domain, d): d for d in domains}
                for future in concurrent.futures.as_completed(future_map):
                    d = future_map[future]
                    try:
                        results.append(future.result().to_dict())
                    except Exception as exc:
                        results.append(make_result(d, "", "error", "all_failed", str(exc)).to_dict())

            # 按请求顺序
            order = {d.strip().lower(): i for i, d in enumerate(domains)}
            results.sort(key=lambda r: order.get(r["domain"], 999))

            self._send_json(200, {
                "results": results,
                "bootstrap_loaded": len(_bootstrap_cache),
                "max_domains": MAX_DOMAINS_PER_REQUEST,
            })
        except Exception as e:
            self._send_json(500, {"error": f"内部错误: {str(e)}"})

    def do_GET(self):
        """健康检查"""
        self._send_json(200, {
            "status": "ok",
            "bootstrap_loaded": len(_bootstrap_cache),
            "sample_tlds": list(_bootstrap_cache.keys())[:10] if _bootstrap_cache else [],
        })

    def _send_json(self, status_code: int, data: dict):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
