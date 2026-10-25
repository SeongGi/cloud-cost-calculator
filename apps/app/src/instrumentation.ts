// Runs once at server start, before any route module loads: no usage data leaves this server.
export function register() {
  process.env.COPILOTKIT_TELEMETRY_DISABLED = "true";
  process.env.DO_NOT_TRACK = "1";
  process.env.NEXT_TELEMETRY_DISABLED = "1";
}
