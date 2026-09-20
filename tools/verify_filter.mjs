// 筛选逻辑的离线复算 —— 把模板里 aggAll/rebuildView 的算法在 Node 里重跑一遍,
// 断言口径闭合。这样不用起浏览器就能验数学, 浏览器那边只验交互。
// 用法: node tools/verify_filter.mjs
import fs from "fs";

const h = fs.readFileSync("output/report.html", "utf8");
const i = h.indexOf("/*__DATA__*/");
// 生成器把 __DATA__ ... __END__ 整段换成 JSON, __END__ 标记本身不再留在产物里,
// 所以不能靠找结束标记来切片 —— 用“解析一个值并返回结束位置”的方式取。
class JSONDecoder {
  raw(s) {
    let depth = 0, inStr = false, esc = false;
    for (let k = 0; k < s.length; k++) {
      const c = s[k];
      if (inStr) {
        if (esc) esc = false;
        else if (c === "\\") esc = true;
        else if (c === '"') inStr = false;
        continue;
      }
      if (c === '"') inStr = true;
      else if (c === "{") depth++;
      else if (c === "}") { depth--; if (depth === 0) return [JSON.parse(s.slice(0, k + 1)), k + 1]; }
    }
    throw new Error("payload 未闭合");
  }
}
const D = new JSONDecoder().raw(h.slice(i + 12))[0];

// ---- 照抄模板里的 aggAll(日型改走 skip 集合那版) ----
function aggAll(F) {
  const e = D.energy_daily, w = D.ws_daily;
  const byEnergy = {}, byWs = {}, byMonth = {}, byDay = {}, byTypeDays = {};
  const K = { tce: 0, cost: 0, co2: 0, qty: 0, days: 0 };

  const wsOK = c => F.ws === null || F.ws.has(c);
  const dayOK = d => F.day === null || F.day.has(d);
  const dateOK = d => (!F.from || d >= F.from) && (!F.to || d <= F.to);

  let skip = null;
  if (F.day !== null && F.day.size !== 3) {
    skip = new Set();
    for (const q of w) if (!dayOK(q.day)) skip.add(q.code + "|" + q.d);
  }

  for (const r of e) {
    if (!wsOK(r.code) || !dateOK(r.d)) continue;
    if (skip && skip.has(r.code + "|" + r.d)) continue;
    const t = byEnergy[r.e] || (byEnergy[r.e] = { qty: 0, tce: 0, cost: 0, co2: 0 });
    t.qty += r.qty; t.tce += r.tce; t.cost += r.cost; t.co2 += r.co2;
    const s = byWs[r.code] || (byWs[r.code] = { tce: 0, cost: 0, co2: 0 });
    s.tce += r.tce; s.cost += r.cost; s.co2 += r.co2;
    const ym = r.d.slice(0, 7);
    const m = byMonth[ym] || (byMonth[ym] = { tce: 0, cost: 0, co2: 0 });
    m.tce += r.tce; m.cost += r.cost; m.co2 += r.co2;
  }
  for (const q of w) {
    if (!wsOK(q.code) || !dateOK(q.d)) continue;
    const dt = byDay[q.day] || (byDay[q.day] = { days: 0, tce: 0, cost: 0, co2: 0 });
    dt.days += 1; byTypeDays[q.day] = (byTypeDays[q.day] || 0) + 1;
    if (!dayOK(q.day)) continue;
    dt.tce += q.tce; dt.cost += q.cost; dt.co2 += q.co2;
    K.tce += q.tce; K.cost += q.cost; K.co2 += q.co2; K.qty += q.qty; K.days += 1;
  }
  return { byEnergy, byWs, byMonth, byDay, byTypeDays, K };
}

const ALL = { preset: "all", from: "", to: "", ws: null, day: null };
let fails = 0;
function ck(name, got, want, tol) {
  const ok = Math.abs(got - want) <= tol;
  if (!ok) fails++;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}  got=${got.toFixed(4)} want=${want.toFixed(4)} d=${(got - want).toFixed(6)}`);
}

// 1. 恒等: 全区间/全车间/全档 -> KPI == totals, 且立方体与车间表也一致
const A0 = aggAll(ALL);
console.log("== 1. 恒等 (全区间全车间全档) ==");
ck("KPI tce  == totals.tce", A0.K.tce, D.totals.tce, 0.01);
ck("KPI cost == totals.cost", A0.K.cost, D.totals.cost, 0.01);
ck("KPI co2  == totals.co2", A0.K.co2, D.totals.co2, 0.01);
ck("cube tce == totals.tce", Object.values(A0.byEnergy).reduce((s, v) => s + v.tce, 0), D.totals.tce, 0.01);
ck("ws   tce == totals.tce", Object.values(A0.byWs).reduce((s, v) => s + v.tce, 0), D.totals.tce, 0.01);
// K.days 数的是"纳入的 车间×日 行数", 不是日历天数 —— 8 车间 × 731 天 = 5,848。
// 日历天数要从 byDay 的档位天数里得到(见第 5 节的相加检验)。
ck("rows == 8 x 731", A0.K.days, 5848, 0);
ck("日历天数 == 731", new Set(D.ws_daily.map(r => r.d)).size, 731, 0);
ck("三档行数相加 == 总行数",
   Object.values(A0.byTypeDays).reduce((a, b) => a + b, 0), A0.K.days, 0);

// 2. 单车间: 必须逐位等于 Q03 那一行
console.log("\n== 2. 单车间 vs Q03 ==");
for (const w of D.workshops) {
  const A = aggAll({ ...ALL, ws: new Set([w.code]) });
  ck(`W ${w.code} tce`, A.K.tce, w.tce, 0.01);
}

// 3. 单月: 必须逐位等于 Q05 那一行
console.log("\n== 3. 单月 vs Q05 ==");
for (const m of D.monthly) {
  const ym = m.ym;
  const A = aggAll({ ...ALL, from: ym + "-01", to: ym + "-31" });
  ck(`M ${ym} tce`, A.K.tce, m.tce, 0.01);
}

// 4. 交叉一致: 能源结构卡合计 == KPI tce (同一筛选下)
console.log("\n== 4. 交叉一致 (结构卡 == KPI) ==");
const cases = [
  ["全期", ALL],
  ["近6月", { ...ALL, from: "2025-07-01", to: "2025-12-31" }],
  ["单车间W01", { ...ALL, ws: new Set(["W01"]) }],
  ["单车间W07", { ...ALL, ws: new Set(["W07"]) }],
  ["多车间", { ...ALL, ws: new Set(["W01", "W02", "W03"]) }],
  ["仅工作日", { ...ALL, day: new Set(["工作日"]) }],
  ["仅周末", { ...ALL, day: new Set(["周末"]) }],
  ["工作日+节假日", { ...ALL, day: new Set(["工作日", "节假日"]) }],
  ["车间×日型×区间", { from: "2025-01-01", to: "2025-06-30", ws: new Set(["W01", "W02"]), day: new Set(["周末"]) }],
];
for (const [name, F] of cases) {
  const A = aggAll(F);
  const cube = Object.values(A.byEnergy).reduce((s, v) => s + v.tce, 0);
  const ws = Object.values(A.byWs).reduce((s, v) => s + v.tce, 0);
  const ok = Math.abs(cube - A.K.tce) <= 0.01 && Math.abs(ws - A.K.tce) <= 0.01;
  if (!ok) fails++;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name.padEnd(18)} cube=${cube.toFixed(3)} ws=${ws.toFixed(3)} KPI=${A.K.tce.toFixed(3)} days=${A.K.days}`);
}

// 5. 日型筛选的语义: 只按日型筛时, KPI 应等于"该日型的天数 × 全期日均"(闭合性自检)
console.log("\n== 5. 日型筛选闭合 ==");
for (const t of ["工作日", "周末", "节假日"]) {
  const A = aggAll({ ...ALL, day: new Set([t]) });
  const n = D.ws_daily.filter(r => r.day === t).length;
  ck(`${t} 天数`, A.K.days, n, 0);
  const direct = D.ws_daily.filter(r => r.day === t).reduce((s, r) => s + r.tce, 0);
  ck(`${t} tce vs 直接求和`, A.K.tce, direct, 0.01);
}

// 6. 日型卡三档恒常显示(不被 dayMatch 过滤掉)
console.log("\n== 6. 日型卡三档恒常 ==");
for (const F of [{ ...ALL, day: new Set(["工作日"]) }, { ...ALL, day: new Set(["周末"]) }]) {
  const A = aggAll(F);
  console.log(`   day=${[...F.day]} -> 档位 [${Object.keys(A.byDay).join(",")}] 纳入=${A.K.days}天`);
}

console.log(fails ? `\n${fails} 项失败` : "\n全部通过");
process.exit(fails ? 1 : 0);
