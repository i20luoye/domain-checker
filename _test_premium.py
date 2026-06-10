import importlib.util
spec = importlib.util.spec_from_file_location('c', 'api/check.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

print('=== 4 字符（应识别为溢价的） ===')
for d in ['abcd','aaaa','abab','aabb','aeio','anna','noon','test','home','cool','fire','java','ruby','rust','milk']:
    print(f'{d:6s} -> {m.get_premium_reason(d + ".com")}')

print('=== 4 字符（应识别为非溢价的） ===')
for d in ['qwer','qzqx','asdf','zxcv','xkcd','bcde','xvds','mhjk','plok','rhyt']:
    print(f'{d:6s} -> {m.get_premium_reason(d + ".com")}')

print('=== 5/6/7 字符 ===')
for d in ['apple','hello','world','setup','unique','wonder','qzqxqzzq']:
    print(f'{d:8s} -> {m.get_premium_reason(d + ".com")}')

print('=== 短域名（1-3 字符） ===')
for d in ['a','ab','abc','xy','go','app','top','vip']:
    print(f'{d:6s} -> {m.get_premium_reason(d + ".com")}')
