# Next.js app (standalone output)
FROM node:22-alpine AS base

FROM base AS deps
RUN apk add --no-cache libc6-compat
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@9.15.9 --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml turbo.json ./
COPY patches ./patches
COPY apps/app/package.json ./apps/app/
RUN pnpm install --frozen-lockfile
# The CopilotKit input patch (Korean/Japanese IME on phones) must be applied.
RUN grep -rq 'dataset.composing' node_modules/.pnpm/@copilotkit+react-core@*/node_modules/@copilotkit/react-core/dist/
# @ag-ui/client's ESM build does `import * as x from "fast-json-patch"` (a CJS module), so
# x.applyPatch is undefined under Node and state patches fail. Use the default export.
RUN find node_modules/.pnpm -path '*@ag-ui/client/dist/index.mjs' -exec \
    sed -i -E 's/import\*as ([A-Za-z_$]+) from"fast-json-patch"/import \1 from"fast-json-patch"/' {} + \
 && ! grep -rl 'import\*as [A-Za-z_$]* from"fast-json-patch"' node_modules/.pnpm/@ag-ui+client*/node_modules/@ag-ui/client/dist/index.mjs

FROM base AS builder
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@9.15.9 --activate
COPY --from=deps /app/node_modules ./node_modules
COPY --from=deps /app/apps/app/node_modules ./apps/app/node_modules
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml turbo.json ./
COPY apps/app ./apps/app
ENV NEXT_TELEMETRY_DISABLED=1
RUN pnpm --filter @repo/app build

FROM base AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 DO_NOT_TRACK=1 COPILOTKIT_TELEMETRY_DISABLED=1
RUN addgroup --system --gid 1001 nodejs && adduser --system --uid 1001 nextjs
COPY --from=builder /app/apps/app/public ./apps/app/public
COPY --from=builder --chown=nextjs:nodejs /app/apps/app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/apps/app/.next/static ./apps/app/.next/static
USER nextjs
EXPOSE 3000
ENV PORT=3000 HOSTNAME=0.0.0.0
CMD ["node", "apps/app/server.js"]
