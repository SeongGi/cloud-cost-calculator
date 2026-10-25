// Decorative pieces of the cost card: group colors and icons, the share donut, the cloud mark.
// Money is never computed here; shares are display-only ratios of BigInt cents.

const GROUP_KEY: Record<string, string> = {
  컴퓨트: "compute",
  서버리스: "serverless",
  데이터베이스: "database",
  캐시: "cache",
  스토리지: "storage",
  네트워크: "network",
  보안: "security",
  모니터링: "monitoring",
};

export function groupKey(group: string): string {
  return GROUP_KEY[group] ?? "other";
}

/** CSS color of a service group (defined as --cc-g-* tokens in cost-card.css). */
export function groupColor(group: string): string {
  return `var(--cc-g-${groupKey(group)})`;
}

/** Share of `part` in `whole` as a percentage with one decimal (display only). */
export function sharePercent(part: bigint, whole: bigint): number {
  return whole > 0n ? Number((part * 1000n) / whole) / 10 : 0;
}

const ICON_PATHS: Record<string, string> = {
  compute: "M7 7h10v10H7z M9.5 3v4 M14.5 3v4 M9.5 17v4 M14.5 17v4 M3 9.5h4 M3 14.5h4 M17 9.5h4 M17 14.5h4",
  serverless: "M13 2 5 13.5h6L10 22l9-12h-6z",
  database: "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6 M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
  cache: "M12 3v3 M12 18v3 M3 12h3 M18 12h3 M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z M5.6 5.6l2.1 2.1 M16.3 16.3l2.1 2.1 M5.6 18.4l2.1-2.1 M16.3 7.7l2.1-2.1",
  storage: "M3 7.5 12 3l9 4.5-9 4.5z M3 7.5v9L12 21l9-4.5v-9 M12 12v9",
  network: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z M3 12h18 M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z",
  security: "M12 3 4.5 6v5.5c0 4.6 3.2 8.3 7.5 9.5 4.3-1.2 7.5-4.9 7.5-9.5V6z M8.8 12l2.2 2.2 4.2-4.4",
  monitoring: "M3 12h4l2.5-6 5 12L17 12h4",
  other: "M5 12h.01 M12 12h.01 M19 12h.01",
};

export function GroupIcon({ group, size = 20 }: { group: string; size?: number }) {
  return (
    <svg
      className="cc-icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={ICON_PATHS[groupKey(group)]} />
    </svg>
  );
}

const CLOUD_MARK: Record<string, string> = { aws: "AWS", azure: "Az", gcp: "GC" };

/** A neutral text mark per cloud (no vendor logos). */
export function CloudMark({ cloud }: { cloud: string }) {
  return (
    <span className="cc-cloud-mark" data-cloud={cloud} aria-hidden="true">
      {CLOUD_MARK[cloud] ?? cloud.slice(0, 2).toUpperCase()}
    </span>
  );
}

// r = 100 / (2π) makes the circumference 100, so dash lengths are plain percentages.
const R = 15.9155;

export function ShareDonut({
  groups,
  total,
  centerLabel,
  centerValue,
}: {
  groups: [string, bigint][];
  total: bigint;
  centerLabel: string;
  centerValue: string;
}) {
  let start = 0;
  const segments = groups.map(([name, value]) => {
    // A group with any cost stays visible, matching the legend's minimum bar.
    const pct = value > 0n ? Math.max(sharePercent(value, total), 0.4) : 0;
    const seg = { name, pct, offset: 25 - start };
    start += pct;
    return seg;
  });
  // A hairline gap between segments, unless one group is the whole donut.
  const gap = segments.length > 1 ? 0.6 : 0;
  // The center text is HTML over the SVG so it keeps a real font size (SVG text would scale down).
  return (
    <div className="cc-donut" role="img" aria-label={`${centerLabel} ${centerValue}, 서비스군 비중은 옆 목록 참조`}>
      <svg viewBox="0 0 42 42" aria-hidden="true">
        <circle cx="21" cy="21" r={R} fill="none" stroke="var(--cc-track)" strokeWidth="6" />
        {segments.map((s) =>
          s.pct > 0 ? (
            <circle
              key={s.name}
              className="cc-donut-seg"
              cx="21"
              cy="21"
              r={R}
              fill="none"
              stroke={groupColor(s.name)}
              strokeWidth="6"
              strokeDasharray={`${Math.max(s.pct - gap, 0.4)} ${100 - Math.max(s.pct - gap, 0.4)}`}
              strokeDashoffset={s.offset}
            />
          ) : null,
        )}
      </svg>
      <div className="cc-donut-center" aria-hidden="true">
        <span>{centerLabel}</span>
        <strong style={centerValue.length > 9 ? { fontSize: "15px" } : undefined}>{centerValue}</strong>
      </div>
    </div>
  );
}
