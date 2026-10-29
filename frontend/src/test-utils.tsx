import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";

const ENVELOPE = Symbol("reply");

/** Explicit HTTP response from a mock handler (plain objects are returned as 200 JSON). */
export function reply(status: number, json?: unknown) {
  return { [ENVELOPE]: true, status, json };
}

type Handler = (body: unknown, url: URL) => unknown;

/** Route-table fetch mock: keys are "METHOD /api/path" (query string ignored). Unmatched calls fail the test. */
export function mockApi(routes: Record<string, Handler>) {
  const calls: { method: string; path: string; url: URL; body: unknown }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://127.0.0.1");
    const method = (init?.method ?? "GET").toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path: url.pathname, url, body });
    const handler = routes[`${method} ${url.pathname}`];
    if (!handler) throw new Error(`unmocked ${method} ${url.pathname}`);
    const out = handler(body, url) as { [ENVELOPE]?: true; status: number; json: unknown } | undefined;
    const isEnvelope = !!out && typeof out === "object" && ENVELOPE in out;
    const status = isEnvelope ? out.status : 200;
    const json = isEnvelope ? out.json : out;
    return new Response(status === 204 ? null : JSON.stringify(json ?? null), { status });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

export function renderAt(ui: ReactElement, path = "/", pattern = "*") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path={pattern} element={ui} /></Routes>
    </MemoryRouter>,
  );
}
