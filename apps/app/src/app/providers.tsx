"use client";

import { CopilotKit } from "@copilotkit/react-core";
import { CopilotChatConfigurationProvider } from "@copilotkit/react-core/v2";
import { ThemeProvider } from "@/hooks/use-theme";

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider>
      <CopilotKit runtimeUrl="/api/copilotkit" showDevConsole={false} enableInspector={false}>
        <CopilotChatConfigurationProvider>{children}</CopilotChatConfigurationProvider>
      </CopilotKit>
    </ThemeProvider>
  );
}
