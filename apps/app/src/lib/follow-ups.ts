// Follow-up questions to offer under an answer, picked from the latest cost result itself (no model
// call): what the configuration lacks or could change. Each one is a question the agent can merge
// into the configuration ("여기에 VPN 추가하면?") or price anew ("같은 구성을 GCP로 하면?").

interface Line {
  key: string;
  qty: number;
  included?: boolean;
  quantity_unit?: string;
}

interface Result {
  state?: string;
  cloud?: string;
  region?: string;
  lines?: Line[];
}

type Msg = { role?: string; content?: unknown };

// Names carry their object particle: "EC2를", "VM을", "Cloud SQL을".
const COMPUTE: Record<string, [RegExp, string]> = {
  aws: [/^ec2\./, "EC2를"],
  azure: [/^vm\./, "VM을"],
  gcp: [/^gce\./, "VM을"],
};
const COMPUTE_ANY: Record<string, RegExp> = { aws: /^(ec2|eks|fargate)\./, azure: /^(vm|aks)\./, gcp: /^(gce|gke|cloud_run)\./ };
type Named = Record<string, [RegExp, string]>;
const LB: Named = {
  aws: [/^(alb|nlb)\./, "ALB"],
  azure: [/^(appgw|lb)\./, "Application Gateway"],
  gcp: [/^cloud_lb\./, "Cloud Load Balancing"],
};
const CACHE: Named = {
  aws: [/^elasticache\./, "ElastiCache"],
  azure: [/^redis\./, "Azure Cache for Redis"],
  gcp: [/^memorystore\./, "Memorystore"],
};
const WAF: Named = {
  aws: [/^waf\./, "AWS WAF"],
  azure: [/^firewall\./, "Azure Firewall"],
  gcp: [/^cloud_armor\./, "Cloud Armor"],
};
const NAT: Named = { aws: [/^nat_gateway\./, "NAT Gateway"], gcp: [/^cloud_nat\./, "Cloud NAT"] };
const LOGS: Named = {
  aws: [/^cloudwatch\./, "CloudWatch"],
  azure: [/^loganalytics\./, "Log Analytics"],
  gcp: [/^cloud_logging\./, "Cloud Logging"],
};
const STORAGE_NAME: Record<string, string> = { aws: "S3", azure: "Blob Storage", gcp: "Cloud Storage" };
const VPN = /^(vpn\.|cloud_vpn\.)/;
const DB: Record<string, { pattern: RegExp; name: string; mysql: RegExp }> = {
  aws: { pattern: /^(rds\.(mysql|postgres)\.|aurora\.)/, name: "RDS를", mysql: /^rds\.mysql\./ },
  azure: { pattern: /^(mysql|postgresql|sqldb)\.(?!storage$)/, name: "DB를", mysql: /^mysql\./ },
  gcp: { pattern: /^cloudsql\.(mysql|postgres)\./, name: "Cloud SQL을", mysql: /^cloudsql\.mysql\./ },
};
const SWAPPABLE = /^(rds\.(mysql|postgres)\.|(mysql|postgresql)\.(?!storage$)|cloudsql\.(mysql|postgres)\.)/;
const STORAGE: Record<string, RegExp> = { aws: /^s3\.storage$/, azure: /^blob\./, gcp: /^gcs\.standard\.storage$/ };
const SEOUL = new Set(["ap-northeast-2", "koreacentral", "asia-northeast3"]);
const OTHER_CLOUD: Record<string, string> = { aws: "Azure", azure: "GCP", gcp: "AWS" };

/** The latest successful cost result in the conversation, if any. */
export function latestResult(messages: readonly unknown[]): Result | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i] as Msg;
    if (m.role !== "tool" || typeof m.content !== "string" || !m.content.includes('"total_cents"')) continue;
    try {
      const r = JSON.parse(m.content) as Result;
      if (r.state === "ok" && r.cloud && Array.isArray(r.lines)) return r;
    } catch {
      // not a cost result
    }
  }
  return null;
}

export function followUps(result: Result | null, max = 4): string[] {
  if (!result?.cloud || !result.lines) return [];
  const cloud = result.cloud;
  const lines = result.lines.filter((l) => l.included !== false);
  const has = (re: RegExp) => lines.find((l) => re.test(l.key));
  const never = /$^/;
  const adds: string[] = [];
  const changes: string[] = [];
  const moves: string[] = [];

  const [computeRe, computeName] = COMPUTE[cloud] ?? [never, ""];
  const compute = has(computeRe);
  const anyCompute = compute || has(COMPUTE_ANY[cloud] ?? never);
  const db = DB[cloud];
  const dbLine = db && has(db.pattern);
  const [lbRe, lbName] = LB[cloud] ?? [never, ""];
  const [cacheRe, cacheName] = CACHE[cloud] ?? [never, ""];
  const [wafRe, wafName] = WAF[cloud] ?? [never, ""];
  const [natRe, natName] = NAT[cloud] ?? [never, ""];
  const [logsRe, logsName] = LOGS[cloud] ?? [never, ""];
  const storage = has(STORAGE[cloud] ?? never);

  if (!has(VPN)) adds.push("여기에 VPN 연결 추가하면?");
  if (!has(lbRe) && anyCompute) adds.push(`앞에 ${lbName} 붙이면?`);
  if (db && !dbLine && anyCompute) adds.push(`여기에 ${{ aws: "RDS PostgreSQL", azure: "PostgreSQL", gcp: "Cloud SQL PostgreSQL" }[cloud]} 추가하면?`);
  if (!has(cacheRe) && dbLine) adds.push(`여기에 ${cacheName} 캐시 추가하면?`);
  if (!has(wafRe) && has(lbRe)) adds.push(`${wafName}로 보안 정책 추가하면?`);
  if (natName && !has(natRe) && anyCompute) adds.push(`${natName} 추가하면?`);
  if (!storage && STORAGE_NAME[cloud]) adds.push(`${STORAGE_NAME[cloud]} 500GB 추가하면?`);
  if (!has(logsRe) && anyCompute) adds.push(`${logsName}로 로그 수집하면?`);

  if (compute) changes.push(`${computeName} ${Math.max(2, compute.qty * 2)}대로 늘리면?`);
  if (db && dbLine && SWAPPABLE.test(dbLine.key)) changes.push(`${db.name} ${db.mysql.test(dbLine.key) ? "PostgreSQL" : "MySQL"}로 바꾸면?`);
  if (storage && storage.quantity_unit === "gb") changes.push(`스토리지를 ${(storage.qty * 2).toLocaleString("en-US")}GB로 늘리면?`);

  moves.push(result.region && SEOUL.has(result.region) ? "도쿄 리전이면 얼마야?" : "서울 리전이면 얼마야?");
  moves.push(`같은 구성을 ${OTHER_CLOUD[cloud] ?? "다른 클라우드"}로 하면 얼마야?`);

  const out: string[] = [];
  for (let i = 0; out.length < max && (i < adds.length || i < changes.length || i < moves.length); i++) {
    for (const list of [adds, changes, moves]) if (list[i] && out.length < max) out.push(list[i]);
  }
  return out;
}
