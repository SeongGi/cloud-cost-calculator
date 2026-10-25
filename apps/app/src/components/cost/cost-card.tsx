"use client";

import { useMemo, useState } from "react";
import { formatUnitPrice, formatUsd, parseQuantity, pricePerUnit, subtotalCents } from "./price-math";
import type { CostEstimate, CostLine, PriceOption } from "./types";
import "./cost-card.css";

const UNIT_WORD: Record<string, string> = {
  count: "개",
  lcu: "LCU",
  endpoint_az: "엔드포인트·AZ",
  vcpu: "vCPU",
  vcore: "vCore",
  memory_gb: "GB 메모리",
  capacity_unit: "용량 단위",
  ru_100: "×100 RU/s",
  gb: "GB",
  million_requests: "백만 건",
  million_queries: "백만 쿼리",
  million_ios: "백만 IO",
  million_units: "백만 단위",
  million_gb_seconds: "백만 GB-초",
};
const VISIBLE_ROWS = 8;

interface RowState {
  qty: number;
  text: string;
  key: string;
  included: boolean;
  sliderMax: number;
}

function selectedOption(line: CostLine, key: string): PriceOption {
  return line.alternatives?.find((a) => a.key === key) ?? line;
}

function cents(line: CostLine, row: RowState): bigint {
  if (!row.included) return 0n;
  const opt = selectedOption(line, row.key);
  return subtotalCents(opt.usd, opt.scale, opt.hours, row.qty);
}

export function formulaText(opt: PriceOption, qty: number): string {
  const unit = UNIT_WORD[opt.quantity_unit] ?? opt.quantity_unit;
  const amount = formatUsd(subtotalCents(opt.usd, opt.scale, opt.hours, qty));
  const q = qty.toLocaleString("en-US");
  if (opt.hours > 1) return `${q}${unit} × ${opt.hours}시간 × ${formatUnitPrice(opt.usd)} = ${amount}`;
  return `${q}${unit} × ${formatUnitPrice(pricePerUnit(opt.usd, opt.scale))}/${unit} = ${amount}`;
}

function initialRows(lines: CostLine[]): Record<string, RowState> {
  return Object.fromEntries(
    lines.map((l) => [
      l.id,
      { qty: l.default_qty, text: String(l.default_qty), key: l.key, included: l.included, sliderMax: l.slider.max },
    ]),
  );
}

export function CostCard({ estimate }: { estimate: CostEstimate }) {
  const [rows, setRows] = useState(() => initialRows(estimate.lines));

  const subtotals = useMemo(
    () => Object.fromEntries(estimate.lines.map((l) => [l.id, cents(l, rows[l.id])])),
    [estimate.lines, rows],
  );
  const total = Object.values(subtotals).reduce((a, b) => a + b, 0n);

  const groups = useMemo(() => {
    const sums = new Map<string, bigint>();
    for (const l of estimate.lines) sums.set(l.group, (sums.get(l.group) ?? 0n) + subtotals[l.id]);
    return [...sums].filter(([, v]) => v > 0n).sort((a, b) => (b[1] > a[1] ? 1 : b[1] < a[1] ? -1 : a[0].localeCompare(b[0])));
  }, [estimate.lines, subtotals]);
  const maxGroup = groups[0]?.[1] ?? 1n;

  // Order rows once by their initial cost so they do not jump while sliding.
  const ordered = useMemo(
    () => [...estimate.lines].sort((a, b) => b.subtotal_cents - a.subtotal_cents || a.id.localeCompare(b.id)),
    [estimate.lines],
  );

  if (estimate.state !== "ok") {
    return (
      <section className="cost-card" data-cost-card="unavailable">
        <p className="cost-message">
          {estimate.state === "price_data_unavailable"
            ? `${estimate.cloud_label ?? estimate.cloud} 가격표가 아직 준비되지 않아 계산하지 않았습니다. 가격을 추측해 보여 주지 않습니다.`
            : `요청을 계산 항목으로 바꾸지 못했습니다. 서비스 이름과 수량을 조금 더 구체적으로 적어 주세요.`}
        </p>
      </section>
    );
  }

  const update = (id: string, patch: Partial<RowState>) => setRows((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));
  const reset = () => setRows(initialRows(estimate.lines));

  const renderRow = (line: CostLine) => {
    const row = rows[line.id];
    const opt = selectedOption(line, row.key);
    const unit = UNIT_WORD[opt.quantity_unit] ?? opt.quantity_unit;
    const invalid = parseQuantity(row.text, line.slider) === null;
    const errorId = `${line.id}-qty-error`;
    return (
      <li key={line.id} className="cost-row" data-line={line.key}>
        <div className="cost-row-head">
          <span className="cost-row-label">
            <span className="cost-chip">{line.service_label}</span> {opt.label}
          </span>
          <span className="cost-row-amount">{row.included ? `${formatUsd(subtotals[line.id])}/월` : "합계 제외"}</span>
        </div>
        {!line.included && (
          <label className="cost-include">
            <input
              type="checkbox"
              checked={row.included}
              onChange={(e) => update(line.id, { included: e.target.checked })}
            />
            <span>합계에 포함 ({opt.label} 기준)</span>
          </label>
        )}
        {line.alternatives && (
          <label className="cost-select">
            <span>사양 선택</span>
            <select
              value={row.key}
              onChange={(e) => update(line.id, { key: e.target.value, included: true })}
            >
              {line.alternatives.map((a) => (
                <option key={a.key} value={a.key}>
                  {a.label} ({formatUsd(subtotalCents(a.usd, a.scale, a.hours, 1))}/{UNIT_WORD[a.quantity_unit] ?? a.quantity_unit}·월)
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="cost-qty">
          <input
            type="range"
            min={line.slider.min}
            max={row.sliderMax}
            step={(row.qty - line.slider.min) % line.slider.step ? 1 : line.slider.step}
            value={row.qty}
            aria-label={`${opt.label} 수량`}
            onChange={(e) => update(line.id, { qty: Number(e.target.value), text: e.target.value })}
          />
          <input
            type="text"
            inputMode="numeric"
            className="cost-qty-input"
            value={row.text}
            aria-label={`${opt.label} 수량 직접 입력`}
            aria-invalid={invalid || undefined}
            aria-describedby={invalid ? errorId : undefined}
            onChange={(e) => {
              const parsed = parseQuantity(e.target.value, line.slider);
              update(
                line.id,
                parsed === null
                  ? { text: e.target.value }
                  : { text: e.target.value, qty: parsed, sliderMax: Math.max(row.sliderMax, parsed) },
              );
            }}
            onBlur={() => update(line.id, { text: String(row.qty) })}
          />
          <span className="cost-unit">{unit}</span>
        </div>
        {invalid && (
          <p id={errorId} className="cost-input-error" aria-live="polite">
            {line.slider.min} 이상의 정수를 입력하세요. 합계는 마지막 올바른 값({row.qty}{unit})으로 계산합니다.
          </p>
        )}
        <p className="cost-formula">{formulaText(opt, row.qty)}</p>
        {line.assumed && row.qty === line.default_qty && <p className="cost-assumed">가정: 수량을 말하지 않아 기본값 {line.default_qty}{unit}</p>}
        {line.note && <p className="cost-note">{line.note}</p>}
      </li>
    );
  };

  const visible = ordered.slice(0, VISIBLE_ROWS);
  const rest = ordered.slice(VISIBLE_ROWS);

  return (
    <section className="cost-card" data-cost-card="ok" aria-label="월 비용 계산 결과">
      <header className="cost-head">
        <h3>{estimate.cloud_label} · {estimate.region_label}</h3>
        <span className="cost-badge">기준일 {estimate.as_of}</span>
      </header>

      <div className="cost-total">
        <span className="cost-total-label">월 예상 합계</span>
        <strong className="cost-total-value" data-total>{formatUsd(total)}</strong>
        <span className="cost-total-sub">온디맨드 정가, USD, 세금·할인·프리 티어 제외</span>
      </div>

      {groups.length > 0 && (
        <>
          <h4 className="cost-section">서비스군별 월 비용</h4>
          <ul className="cost-groups">
            {groups.map(([name, value]) => (
              <li key={name}>
                <span className="cost-group-name">{name}</span>
                <span className="cost-bar" aria-hidden="true">
                  <i style={{ width: `${Math.max(3, Number((value * 1000n) / maxGroup) / 10)}%` }} />
                </span>
                <span className="cost-group-value">
                  {formatUsd(value)} <small>({total > 0n ? (Number((value * 1000n) / total) / 10).toFixed(1) : "0.0"}%)</small>
                </span>
              </li>
            ))}
          </ul>
        </>
      )}

      <h4 className="cost-section">항목 조정 <span className="cost-hint">슬라이더나 숫자로 수량을 바꾸면 합계가 다시 계산됩니다</span></h4>
      <ul className="cost-rows">{visible.map(renderRow)}</ul>
      {rest.length > 0 && (
        <details className="cost-more">
          <summary>모두 보기 ({rest.length}개 더)</summary>
          <ul className="cost-rows">{rest.map(renderRow)}</ul>
        </details>
      )}
      <button type="button" className="cost-reset" onClick={reset}>기본값 되돌리기</button>

      {estimate.excluded.length > 0 && (
        <p className="cost-excluded">
          계산에서 제외: {estimate.excluded.map((e) => `${e.service}(${e.reason})`).join(", ")}
        </p>
      )}
      <footer className="cost-foot">
        {estimate.notes.map((n, i) => <p key={i}>{n}</p>)}
        <p>같은 서비스의 사양 대안은 하나만 합산합니다. 함께 붙는 항목(예: 로드밸런서 시간과 LCU)은 모두 더합니다.</p>
        <p>
          출처: {estimate.source_url ? <a href={estimate.source_url} target="_blank" rel="noopener noreferrer">{estimate.source}</a> : estimate.source},
          기준일 {estimate.as_of}. 실제 청구액과 다를 수 있습니다.
        </p>
      </footer>
    </section>
  );
}

export function CostCardPending() {
  return (
    <section className="cost-card cost-pending" data-cost-card="pending" aria-busy="true">
      <p>구성을 계산 항목으로 바꾸는 중입니다. 로컬 AI라 10초 정도 걸릴 수 있습니다.</p>
      <span className="cost-skeleton" />
      <span className="cost-skeleton short" />
    </section>
  );
}
