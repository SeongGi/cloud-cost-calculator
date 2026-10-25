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
const VPN = /^(vpn\.|cloud_vpn\.)/;
const DB: Record<string, { pattern: RegExp; name: string; mysql: RegExp }> = {
  aws: { pattern: /^rds\.(mysql|postgres)\./, name: "RDS를", mysql: /^rds\.mysql\./ },
  azure: { pattern: /^(mysql|postgresql)\.(?!storage$)/, name: "DB를", mysql: /^mysql\./ },
  gcp: { pattern: /^cloudsql\.(mysql|postgres)\./, name: "Cloud SQL을", mysql: /^cloudsql\.mysql\./ },
};
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
  const out: string[] = [];

  if (!has(VPN)) out.push("여기에 VPN 연결 추가하면?");

  const [computeRe, computeName] = COMPUTE[cloud] ?? [/$^/, ""];
  const compute = has(computeRe);
  if (compute) out.push(`${computeName} ${Math.max(2, compute.qty * 2)}대로 늘리면?`);

  const db = DB[cloud];
  const dbLine = db && has(db.pattern);
  if (db && dbLine) out.push(`${db.name} ${db.mysql.test(dbLine.key) ? "PostgreSQL" : "MySQL"}로 바꾸면?`);
  else if (db) out.push(`여기에 ${{ aws: "RDS PostgreSQL", azure: "PostgreSQL", gcp: "Cloud SQL PostgreSQL" }[cloud]} 추가하면?`);

  out.push(`같은 구성을 ${OTHER_CLOUD[cloud] ?? "다른 클라우드"}로 하면 얼마야?`);
  out.push(result.region && SEOUL.has(result.region) ? "도쿄 리전이면 얼마야?" : "서울 리전이면 얼마야?");
  const storage = has(STORAGE[cloud] ?? /$^/);
  if (storage && storage.quantity_unit === "gb") out.push(`스토리지를 ${(storage.qty * 2).toLocaleString("en-US")}GB로 늘리면?`);
  return out.slice(0, max);
}
