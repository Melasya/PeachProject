import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "@/lib/api";

function mockFetch(body: unknown, init: { status?: number } = {}) {
  const status = init.status ?? 200;
  return vi.spyOn(globalThis, "fetch").mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response);
}

afterEach(() => vi.restoreAllMocks());

describe("api", () => {
  it("parses a list response", async () => {
    mockFetch({ items: [], total: 0 });
    await expect(api.listItems()).resolves.toEqual({ items: [], total: 0 });
  });

  it("passes pagination through as query parameters", async () => {
    const spy = mockFetch({ items: [], total: 0 });
    await api.listItems({ limit: 5, offset: 10 });
    expect(spy.mock.calls[0][0]).toContain("/api/v1/items?limit=5&offset=10");
  });

  it("raises ApiError carrying the detail from the backend", async () => {
    mockFetch({ detail: "Item not found" }, { status: 404 });
    await expect(api.listItems()).rejects.toMatchObject({
      status: 404,
      message: "Item not found",
    });
  });

  it("raises ApiError when the network is unreachable", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => {
      throw new TypeError("failed");
    });
    await expect(api.listItems()).rejects.toBeInstanceOf(ApiError);
  });

  it("sends the access token with every meetings call, and none to items", async () => {
    const spy = mockFetch({ meetings: [], total: 0 });
    await api.listMeetings("tok-1", { limit: 3 });
    const [url, init] = spy.mock.calls[0];
    expect(url).toContain("/api/v1/meetings?limit=3");
    expect((init?.headers as Record<string, string>).Authorization).toBe(
      "Bearer tok-1",
    );

    spy.mockClear();
    await api
      .createMeeting("tok-2", {
        title: "t",
        organiser: "o",
        starts_at: "2031-03-04T08:00:00.000Z",
        ends_at: "2031-03-04T09:00:00.000Z",
        participants: [],
        agenda_items: [],
        action_items: [],
      })
      .catch(() => undefined); // the canned list body is not a meeting
    const [, post] = spy.mock.calls[0];
    expect(post?.method).toBe("POST");
    expect((post?.headers as Record<string, string>).Authorization).toBe(
      "Bearer tok-2",
    );

    spy.mockClear();
    mockFetch({ items: [], total: 0 });
    await api.listItems();
    const [, items] = vi.mocked(globalThis.fetch).mock.calls[0];
    expect(items?.headers).not.toHaveProperty("Authorization");
  });

  it("asks the user to sign in again on a 401", async () => {
    mockFetch({ detail: "Sign in to continue" }, { status: 401 });
    await expect(api.listMeetings("expired")).rejects.toMatchObject({
      status: 401,
      message: "Your session has expired - sign in again.",
    });
  });
});
