import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

const agent = { isRunning: true };
let renderTool: (props: { status: string; result?: string }) => ReactNode = () => null;
vi.mock("@copilotkit/react-core/v2", () => ({
  useAgent: () => ({ agent }),
  useRenderTool: (tool: { render: typeof renderTool }) => {
    renderTool = tool.render;
  },
}));

import { useCostTool } from "../use-cost-tool";

function Pending() {
  return <>{renderTool({ status: "executing" })}</>;
}

describe("useCostTool pending card", () => {
  it("stays stopped once the run was seen stopped, even when a new run starts", () => {
    function Host() {
      useCostTool();
      return null;
    }
    render(<Host />);
    agent.isRunning = true;
    const view = render(<Pending />);
    expect(screen.queryByText(/중간에 멈췄습니다/)).toBeNull();

    agent.isRunning = false;
    view.rerender(<Pending />);
    expect(screen.getByText(/중간에 멈췄습니다/)).toBeTruthy();

    agent.isRunning = true;
    view.rerender(<Pending />);
    expect(screen.getByText(/중간에 멈췄습니다/)).toBeTruthy();
  });
});
