import { Component, useMemo, useState, type ReactNode } from "react";
import { z } from "zod";
import { useAgent, useRenderTool } from "@copilotkit/react-core/v2";
import { CostCard, CostCardPending } from "@/components/cost/cost-card";
import { parseEstimate } from "@/components/cost/types";

const EstimateParams = z.object({
  cloud: z.string(),
  region: z.string().nullish(),
  items: z.unknown(),
});

function CostProblem({ title, detail }: { title: string; detail?: string }) {
  return (
    <section className="cost-card" data-cost-card="problem">
      <p className="cost-message">{title}</p>
      {detail && <p className="cost-foot">원인: {detail}</p>}
    </section>
  );
}

// A broken result must not take the whole chat down with it.
class CardBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error("[cost-card] render failed", error);
  }

  render() {
    return this.state.error
      ? <CostProblem title="계산 결과를 그리지 못했습니다. 다시 질문해 주세요." detail={this.state.error.message} />
      : this.props.children;
  }
}

function CostResult({ result }: { result: string }) {
  const parsed = useMemo(() => {
    try {
      return { estimate: parseEstimate(result) };
    } catch (error) {
      console.error("[cost-card] invalid estimate_cost result", error);
      return { error: error instanceof Error ? error.message.slice(0, 200) : String(error) };
    }
  }, [result]);
  if (parsed.error !== undefined) {
    return <CostProblem title="계산 결과 형식이 올바르지 않아 표시하지 않았습니다. 다시 질문해 주세요." detail={parsed.error} />;
  }
  return <CardBoundary><CostCard estimate={parsed.estimate!} /></CardBoundary>;
}

function PendingOrStopped() {
  const { agent } = useAgent();
  // Once a call is seen stopped it stays stopped, even when a later question starts a new run.
  const [stopped, setStopped] = useState(false);
  if (!agent.isRunning && !stopped) setStopped(true);
  return stopped || !agent.isRunning
    ? <CostProblem title="계산이 중간에 멈췄습니다. 같은 질문을 다시 보내 주세요." />
    : <CostCardPending />;
}

// estimate_cost runs on the agent; its JSON result is drawn here as a fixed component.
export function useCostTool() {
  useRenderTool({
    name: "estimate_cost",
    parameters: EstimateParams,
    render: ({ status, result }) =>
      status === "complete" ? <CostResult result={result} /> : <PendingOrStopped />,
  });
}
