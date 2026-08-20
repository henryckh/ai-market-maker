import { flowAuthHeaders, flowApiBase, proxyJson } from "@/server/flowProxy";

export async function GET(_request: Request, context: { params: Promise<{ provider: string }> }) {
  const { provider } = await context.params;
  return proxyJson(`${flowApiBase()}/public/providers/${encodeURIComponent(provider)}/profile`, {
    headers: { ...flowAuthHeaders() },
    fallbackJson: { enabled: false, provider },
  });
}
