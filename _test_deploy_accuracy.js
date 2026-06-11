// 复测脚本：调用 https://dom.ficita.com/api/check 验证修复效果
const https = require('https');

const API = 'https://dom.ficita.com/api/check';

const TEST_DOMAINS = [
  // 100% 已注册（知名大站）
  'google.com', 'github.com', 'openai.com', 'stackoverflow.com',
  'baidu.com', 'apple.com', 'python.org', 'wikipedia.org',
  // 大概率可注册（随机键盘串）
  'qzqxqzzq.com', 'qwerty12345x.com', 'zxcvbnmqwer123.com',
  'xzqzxqzx.com', 'asdfqwerqwerqwerqwer.com',
  // .io
  'github.io', 'gitlab.io', 'a.io',
  // 短字符 .com
  'go.com', 'ai.com', 'car.com',
  // 3字符
  'abc.com', 'xyz.com', 'qwe.com',
  // 不同 TLD
  'qzqx.cn', 'qzqx.org', 'qzqx.io', 'qzqx.ai', 'qzqx.net', 'qzqx.dev', 'qzqx.app',
];

const KNOWN_TAKEN = ['google.com','github.com','openai.com','stackoverflow.com','baidu.com','apple.com','python.org','wikipedia.org','github.io','gitlab.io','go.com','ai.com','car.com','abc.com','xyz.com','qwe.com'];
const KNOWN_AVAIL_LIKELY = ['qzqxqzzq.com','qwerty12345x.com','zxcvbnmqwer123.com','xzqzxqzx.com','asdfqwerqwerqwerqwer.com','qzqx.cn','qzqx.org','qzqx.io','qzqx.ai','qzqx.net','qzqx.dev','qzqx.app'];

function post(body) {
  return new Promise(resolve => {
    const data = JSON.stringify(body);
    const t0 = Date.now();
    const req = https.request(API, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) },
      timeout: 30000,
      rejectUnauthorized: false,
    }, res => {
      let chunks = [];
      res.on('data', c => chunks.push(c));
      res.on('end', () => resolve({ ms: Date.now() - t0, code: res.statusCode, body: Buffer.concat(chunks).toString('utf-8') }));
    });
    req.on('error', e => resolve({ ms: Date.now() - t0, error: e.message }));
    req.on('timeout', () => req.destroy(new Error('TIMEOUT')));
    req.write(data);
    req.end();
  });
}

function get() {
  return new Promise(resolve => {
    const t0 = Date.now();
    const req = https.request(API, { method: 'GET', timeout: 10000, rejectUnauthorized: false }, res => {
      let chunks = [];
      res.on('data', c => chunks.push(c));
      res.on('end', () => resolve({ ms: Date.now() - t0, code: res.statusCode, body: Buffer.concat(chunks).toString('utf-8') }));
    });
    req.on('error', e => resolve({ ms: Date.now() - t0, error: e.message }));
    req.on('timeout', () => req.destroy(new Error('TIMEOUT')));
    req.end();
  });
}

(async () => {
  console.log('='.repeat(80));
  console.log('P1-004: GET /api/check 健康检查');
  console.log('='.repeat(80));
  const h = await get();
  console.log(`HTTP ${h.code}  耗时 ${h.ms}ms`);
  try { console.log(JSON.stringify(JSON.parse(h.body), null, 2)); }
  catch { console.log(h.body.substring(0, 500)); }

  console.log();
  console.log('='.repeat(80));
  console.log(`批量测试：${TEST_DOMAINS.length} 个域名`);
  console.log('='.repeat(80));
  const r = await post({ domains: TEST_DOMAINS });
  console.log(`HTTP ${r.code}  耗时 ${r.ms}ms`);
  if (r.error) { console.log('ERR', r.error); return; }
  let resp;
  try { resp = JSON.parse(r.body); } catch { console.log('JSON parse fail'); console.log(r.body.substring(0, 500)); return; }

  // 顶层字段
  console.log('\n[顶层字段]');
  console.log(`  ok=${resp.ok}  elapsed_ms=${resp.elapsed_ms}  bootstrap_loaded=${resp.bootstrap_loaded}  count=${resp.count}`);

  console.log();
  console.log('P1-005 验证：响应顶层有 elapsed_ms / bootstrap_loaded 字段？');
  const ok4 = resp.elapsed_ms !== undefined;
  const ok5 = resp.bootstrap_loaded !== undefined;
  console.log(`  elapsed_ms: ${ok4 ? '✅' : '❌'}  bootstrap_loaded: ${ok5 ? '✅' : '❌'}`);

  console.log();
  console.log(`${'域名'.padEnd(34)} ${'状态'.padEnd(11)} ${'置信度'.padEnd(11)} ${'溢价'.padEnd(4)} ${'来源'.padEnd(7)} method`);
  console.log('-'.repeat(120));
  for (const x of (resp.results || [])) {
    const d = (x.domain || '?').padEnd(34);
    const st = (x.status || '?').padEnd(11);
    const conf = (x.confidence || '?').padEnd(11);
    const prem = (x.premium ? '是' : '').padEnd(4);
    const src = `${x.sources_ok || '?'}/${x.sources_total || '?'}`.padEnd(7);
    const m = x.method || '';
    console.log(`${d} ${st} ${conf} ${prem} ${src} ${m}`);
  }

  // P0-001 验证
  console.log();
  console.log('P0-001 验证：qzqx.cn 不应被 DNS 预筛误判');
  const qzqxcn = (resp.results || []).find(x => x.domain === 'qzqx.cn');
  if (qzqxcn) {
    const ok = qzqxcn.status === 'available' || (qzqxcn.status === 'taken' && qzqxcn.method.includes('rdap_verify'));
    console.log(`  ${ok ? '✅' : '❌'} qzqx.cn → ${qzqxcn.status}  method=${qzqxcn.method}  confidence=${qzqxcn.confidence}`);
  }

  // P0-002 验证：置信度方向
  console.log();
  console.log('P0-002 验证：置信度评分方向');
  const r2 = resp.results || [];
  const takenWithRdap = r2.filter(x => x.status === 'taken' && (x.method.includes('rdap_verify') || x.method === 'primary' || x.method === 'consensus'));
  const goodTaken = takenWithRdap.filter(x => x.confidence === 'HIGH' || x.confidence === 'VERY_HIGH').length;
  const badTaken = takenWithRdap.filter(x => x.confidence === 'LOW' || x.confidence === 'MEDIUM').length;
  console.log(`  RDAP 确认 taken 评级: 高置信度 ${goodTaken} / 中低 ${badTaken}  ${goodTaken > badTaken ? '✅' : '❌'}`);

  const availRdap = r2.filter(x => x.status === 'available' && (x.method === 'primary' || x.method === 'consensus'));
  const goodAvail = availRdap.filter(x => x.confidence === 'VERY_HIGH').length;
  const badAvail = availRdap.filter(x => x.confidence === 'LOW' || x.confidence === 'MEDIUM').length;
  console.log(`  RDAP available 评级: 极高 ${goodAvail} / 中低 ${badAvail}  ${goodAvail > badAvail ? '✅' : '❌'}`);

  // P1-003 验证
  console.log();
  console.log('P1-003 验证：.io 域名 sources_total 应 ≥ 3');
  const io = r2.filter(x => x.suffix === 'io');
  const ok3 = io.every(x => x.sources_total >= 3);
  console.log(`  ${ok3 ? '✅' : '❌'} .io 域名: ${io.map(x => `${x.domain}(${x.sources_total})`).join(', ')}`);

  // 整体准确率
  console.log();
  console.log('整体准确性（对比已知状态）');
  const falseTaken = r2.filter(x => KNOWN_AVAIL_LIKELY.includes(x.domain) && x.status === 'taken').length;
  const falseAvail = r2.filter(x => KNOWN_TAKEN.includes(x.domain) && x.status === 'available').length;
  console.log(`  可注册误判 taken: ${falseTaken}/${KNOWN_AVAIL_LIKELY.length}`);
  console.log(`  已注册误判 available: ${falseAvail}/${KNOWN_TAKEN.length}`);
})();
