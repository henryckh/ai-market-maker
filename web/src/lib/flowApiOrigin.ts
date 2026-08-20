export function getFlowApiOrigin(): string {
  if (typeof window !== "undefined") {
    return "/api/flow";
  }
  const raw =
    process.env.FLOW_API_BASE_URL?.trim() ||
    process.env.NEXT_PUBLIC_FLOW_API_BASE_URL?.trim() ||
    "http://127.0.0.1:8001";
  return raw.replace(/\/$/, "");
}

export function dashboardFlowHeaders(extra?: HeadersInit): HeadersInit {
  return { "x-aimm-dashboard": "1", ...(extra ?? {}) };
}
