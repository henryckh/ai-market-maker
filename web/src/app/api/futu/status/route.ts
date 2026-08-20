import { NextResponse } from "next/server";
import { flowApiBase, flowAuthHeaders } from "@/server/flowProxy";

/**
 * GET /api/futu/status
 *
 * Proxies to Flow `/futu/status` (OpenD quote connectivity).
 */
export async function GET() {
  const flowBase = flowApiBase();

  try {
    const flowRes = await fetch(`${flowBase}/futu/status`, {
      cache: "no-store",
      signal: AbortSignal.timeout(10_000),
      headers: { ...flowAuthHeaders() },
    });
    const data = (await flowRes.json().catch(() => ({}))) as Record<string, unknown>;
    if (flowRes.ok) {
      return NextResponse.json({ ...data, source: (data.source as string | undefined) ?? "flow" });
    }

    const hint401 =
      flowRes.status === 401
        ? "Flow rejected the request. Check AIMM_API_KEY on the API."
        : undefined;

    return NextResponse.json(
      {
        error: "futu_upstream_error",
        httpStatus: flowRes.status,
        ...data,
        ...(hint401 ? { hint: hint401 } : {}),
      },
      { status: flowRes.status },
    );
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json(
      {
        error: "futu_flow_unreachable",
        detail: msg,
        flowBase,
        opend_connected: false,
        status: "error",
        hint:
          "No response from Flow. Local dev: start uvicorn on 8001 and set FLOW_API_BASE_URL in web/.env.local. Docker: web must call the api service at http://api:8001 (compose sets this); do not rely on 127.0.0.1 from the web container.",
      },
      { status: 503 },
    );
  }
}
