"use client";

import { useEffect, useState } from "react";
import { fetchNexusPayloadWithSource } from "@/lib/api/traces";
import type { NexusPayload } from "@/types/nexus-payload";
import mockTraces from "@/data/mock-traces.json";
import { getFlowApiOrigin } from "@/lib/flowApiOrigin";

function livePayloadUrl(followId: string): string {
  if (followId === "latest") return "/api/traces";
  return `${getFlowApiOrigin()}/runs/${encodeURIComponent(followId)}/payload?soft=true`;
}

/**
 * @param runId Flow run to follow. Live desk should use `latest-paper` or a concrete
 * `run-…` id. Research panels fetch `/runs/{bt-…}` themselves — do not point Live
 * at `bt-*` so paper and backtest can run in parallel without stream theft.
 */
export function useNexusPayload(runId: string = "latest") {
  const followId = (runId || "latest").trim() || "latest";
  const [payload, setPayload] = useState<NexusPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [wsConnected, setWsConnected] = useState(false);
  const [traceDataSource, setTraceDataSource] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const useMock = process.env.NEXT_PUBLIC_USE_MOCK?.trim() === "1";
    if (useMock) {
      setPayload(mockTraces as NexusPayload);
      setTraceDataSource("mock");
      setError(null);
      setLoading(false);
      setWsConnected(false);
      return () => {
        cancelled = true;
      };
    }

    setLoading(true);
    const httpUrl = livePayloadUrl(followId);

    fetchNexusPayloadWithSource(httpUrl)
      .then(({ payload: data, dataSource }) => {
        if (!cancelled) {
          setPayload(data);
          setTraceDataSource(dataSource ?? "live");
          setError(null);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setPayload(null);
          setTraceDataSource("idle");
          setError(e instanceof Error ? e : new Error(String(e)));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [followId]);

  useEffect(() => {
    const useMock = process.env.NEXT_PUBLIC_USE_MOCK?.trim() === "1";
    if (useMock) return;

    let closed = false;
    const httpUrl = livePayloadUrl(followId);

    const tick = () => {
      if (closed) return;
      fetchNexusPayloadWithSource(httpUrl)
        .then(({ payload: data, dataSource }) => {
          if (closed) return;
          setPayload(data);
          setTraceDataSource(dataSource ?? "live");
          setError(null);
          setWsConnected(true);
          setLoading(false);
        })
        .catch(() => {
          if (!closed) setWsConnected(false);
        });
    };

    const interval = window.setInterval(tick, 1000);
    return () => {
      closed = true;
      setWsConnected(false);
      window.clearInterval(interval);
    };
  }, [followId]);

  return { payload, loading, error, wsConnected, traceDataSource, followRunId: followId };
}
