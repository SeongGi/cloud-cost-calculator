import { z } from "zod";

const Price = z.string().regex(/^\d+(\.\d+)?$/, "price must be a plain decimal string");
const Slider = z.object({ min: z.number().int(), max: z.number().int(), step: z.number().int().positive() });

const PriceOption = z.object({
  key: z.string(),
  label: z.string(),
  usd: Price,
  unit: z.string(),
  quantity_unit: z.string(),
  scale: z.number().int().positive(),
  hours: z.number().int().positive(),
  slider: Slider,
});

const CostLine = PriceOption.extend({
  id: z.string(),
  service: z.string(),
  service_label: z.string(),
  group: z.string(),
  qty: z.number().int().nonnegative(),
  default_qty: z.number().int().nonnegative(),
  assumed: z.boolean(),
  included: z.boolean(),
  family: z.boolean(),
  alternatives: z.array(PriceOption).optional(),
  missing_spec: z.string().optional(),
  note: z.string().optional(),
  subtotal_cents: z.number().int().nonnegative(),
});

const CostEstimate = z.object({
  state: z.enum(["ok", "price_data_unavailable", "invalid_arguments"]),
  cloud: z.string(),
  cloud_label: z.string().optional(),
  region: z.string().optional(),
  region_label: z.string().optional(),
  requested_region: z.string().nullish(),
  currency: z.string().optional(),
  as_of: z.string().optional(),
  source: z.string().optional(),
  source_url: z.string().optional(),
  message: z.string().optional(),
  lines: z.array(CostLine),
  excluded: z.array(z.object({ service: z.string(), reason: z.string() })),
  notes: z.array(z.string()),
  total_cents: z.number().int().nonnegative(),
});

export type Slider = z.infer<typeof Slider>;
export type PriceOption = z.infer<typeof PriceOption>;
export type CostLine = z.infer<typeof CostLine>;
export type CostEstimate = z.infer<typeof CostEstimate>;

/** Validate the whole tool result before anything is drawn; throws with the reason. */
export function parseEstimate(raw: unknown): CostEstimate {
  const value = typeof raw === "string" ? JSON.parse(raw) : raw;
  return CostEstimate.parse(value);
}
