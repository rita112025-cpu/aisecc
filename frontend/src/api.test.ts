import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, qs } from "./api";
import { fmtTime, label, tone } from "./labels";

afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  it("returns parsed JSON", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ok: 1 }), { status: 200 })));
    await expect(api.get("/x")).resolves.toEqual({ ok: 1 });
  });

  it("maps backend detail to a Chinese error message", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "專案不存在" }), { status: 404 })));
    await expect(api.get("/projects/9")).rejects.toMatchObject({ message: "專案不存在", status: 404 });
  });

  it("joins validation error lists", async () => {
    const body = { detail: [{ msg: "欄位必填" }, { msg: "格式錯誤" }] };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { status: 422 })));
    await expect(api.post("/x", {})).rejects.toThrow("欄位必填；格式錯誤");
  });

  it("reports network failures as backend unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    const err = await api.get("/x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toContain("無法連線");
  });

  it("handles 204 and non-JSON bodies", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 204 })));
    await expect(api.del("/x")).resolves.toBeUndefined();
    vi.stubGlobal("fetch", vi.fn(async () => new Response("Internal Server Error", { status: 500 })));
    await expect(api.get("/x")).rejects.toThrow("HTTP 500");
  });

  it("builds query strings skipping empty values", () => {
    expect(qs({ a: 1, b: "", c: undefined, d: null, e: "中 文" })).toBe("?a=1&e=%E4%B8%AD+%E6%96%87");
    expect(qs({})).toBe("");
  });
});

describe("labels", () => {
  it("translates and falls back", () => {
    expect(label.status("needs_authorization")).toBe("needs_authorization");
    expect(label.tool("needs_authorization")).toBe("需授權");
    expect(label.severity("critical")).toBe("嚴重");
    expect(tone("failed")).toBe("bad");
    expect(tone("partial")).toBe("warn");
    expect(fmtTime(null)).toBe("—");
    expect(fmtTime("2026-10-29T08:00:00")).not.toBe("2026-10-29T08:00:00");
  });
});
