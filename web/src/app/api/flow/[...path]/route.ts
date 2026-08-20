import { flowApiBase, flowAuthHeaders } from "@/server/flowProxy";

export const dynamic = "force-dynamic";

const DASHBOARD_HEADER = "x-aimm-dashboard";

const ALLOWED_PREFIXES = [
  "runs/",
  "backtests",
  "engine/",
  "capabilities",
  "ops/",
  "runtime-settings",
  "signals/",
  "deploy-config",
  "agent-prompts",
  "pm/",
  "studio/",
  "futu/",
  "tools",
  "strategies",
];

const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailers",
  "transfer-encoding",
  "upgrade",
  "host",
  "content-length",
]);

function pathAllowed(parts: string[]): boolean {
  if (parts.some((p) => p.includes("..") || p.includes("/") || p.includes("\\"))) {
    return false;
  }
  const joined = parts.join("/");
  return ALLOWED_PREFIXES.some(
    (prefix) => joined === prefix.replace(/\/$/, "") || joined.startsWith(prefix),
  );
}

function isSameOrigin(request: Request): boolean {
  const reqUrl = new URL(request.url);
  const origin = request.headers.get("origin");
  if (origin) {
    try {
      return new URL(origin).host === reqUrl.host;
    } catch {
      return false;
    }
  }
  const referer = request.headers.get("referer");
  if (referer) {
    try {
      return new URL(referer).host === reqUrl.host;
    } catch {
      return false;
    }
  }
  return false;
}

async function proxyToFlow(request: Request, path: string[]): Promise<Response> {
  if (!pathAllowed(path)) {
    return Response.json({ error: "forbidden", hint: "path is not dashboard-proxied" }, { status: 403 });
  }
  const dashboardClient = request.headers.get(DASHBOARD_HEADER) === "1";
  if (!isSameOrigin(request) && !dashboardClient) {
    return Response.json(
      { error: "forbidden", hint: "dashboard proxy is same-origin only" },
      { status: 403 },
    );
  }

  const search = new URL(request.url).search;
  const upstream = `${flowApiBase()}/${path.map(encodeURIComponent).join("/")}${search}`;

  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) {
      headers.set(key, value);
    }
  });
  for (const [key, value] of Object.entries(flowAuthHeaders())) {
    headers.set(key, value);
  }

  const method = request.method.toUpperCase();
  const init: RequestInit = {
    method,
    headers,
    cache: "no-store",
    redirect: "manual",
  };
  if (!["GET", "HEAD"].includes(method)) {
    init.body = await request.arrayBuffer();
  }

  const res = await fetch(upstream, init);
  const outHeaders = new Headers();
  const contentType = res.headers.get("content-type");
  if (contentType) outHeaders.set("content-type", contentType);

  return new Response(res.body, { status: res.status, headers: outHeaders });
}

type Ctx = { params: Promise<{ path: string[] }> };

export async function GET(request: Request, ctx: Ctx) {
  return proxyToFlow(request, (await ctx.params).path ?? []);
}

export async function POST(request: Request, ctx: Ctx) {
  return proxyToFlow(request, (await ctx.params).path ?? []);
}

export async function PUT(request: Request, ctx: Ctx) {
  return proxyToFlow(request, (await ctx.params).path ?? []);
}

export async function PATCH(request: Request, ctx: Ctx) {
  return proxyToFlow(request, (await ctx.params).path ?? []);
}

export async function DELETE(request: Request, ctx: Ctx) {
  return proxyToFlow(request, (await ctx.params).path ?? []);
}
