import { describe, expect, it } from "vitest";
import fixture from "./engine-fixture.json";
import { formatUnitPrice, formatUsd, parseQuantity, pricePerUnit, subtotalCents } from "../price-math";
import { formulaText } from "../cost-card";
import type { CostEstimate } from "../types";

describe("price math", () => {
  it("matches the agent's Decimal math line by line", () => {
    for (const est of fixture as unknown as CostEstimate[]) {
      let total = 0n;
      for (const l of est.lines) {
        const c = l.included ? subtotalCents(l.usd, l.scale, l.hours, l.qty) : 0n;
        expect(c).toBe(BigInt(l.subtotal_cents));
        total += c;
      }
      expect(total).toBe(BigInt(est.total_cents));
    }
  });

  it("rounds half up in cents", () => {
    expect(subtotalCents("0.0225000000", 1, 730, 1)).toBe(1643n);
    expect(subtotalCents("0.0000166667", 1_000_000, 1, 1)).toBe(1667n);
    expect(subtotalCents("1", 1, 1, 0)).toBe(0n);
  });

  it("formats", () => {
    expect(formatUsd(123456789n)).toBe("$1,234,567.89");
    expect(formatUnitPrice("0.0520000000")).toBe("$0.052");
    expect(formatUnitPrice("5.0000000000")).toBe("$5.00");
    expect(pricePerUnit("0.0000003500", 1_000_000)).toBe("0.3500000000");
  });

  it("parses whole quantities, beyond the slider max too", () => {
    const slider = { min: 0, max: 50, step: 1 };
    expect(parseQuantity("12", slider)).toBe(12);
    expect(parseQuantity("51", slider)).toBe(51);
    expect(parseQuantity("-1", slider)).toBeNull();
    expect(parseQuantity("0", { min: 1 })).toBeNull();
    expect(parseQuantity("1.5", slider)).toBeNull();
    expect(parseQuantity("abc", slider)).toBeNull();
  });

  it("builds formulas", () => {
    const opt = { key: "ec2.t3.medium", label: "t3.medium", usd: "0.0520000000", unit: "Hrs", quantity_unit: "count", scale: 1, hours: 730, slider: { min: 0, max: 50, step: 1 } };
    expect(formulaText(opt, 2)).toBe("2개 × 730시간 × $0.052 = $75.92");
    const get = { ...opt, usd: "0.0000003500", quantity_unit: "million_requests", scale: 1_000_000, hours: 1 };
    expect(formulaText(get, 3)).toBe("3백만 건 × $0.35/백만 건 = $1.05");
  });
});
