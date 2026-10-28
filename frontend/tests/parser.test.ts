import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const URL = "https://parser.example.test/";

const answer = {
  agenda: {
    title: "Sprint planning",
    starts_at: "2026-10-19T10:00:00+03:00",
    ends_at: null,
    organiser: "Olena",
    participants: ["Olena", "Taras"],
    agenda_items: [{ title: "Demo", owner: null, duration_minutes: 15 }],
    action_items: [],
  },
  model: "anthropic.claude-opus-5-5",
  requested_model: "anthropic.claude-opus-5-5",
  prompt_version: "agenda-v1",
  attempts: 1,
  usage: { input_tokens: 900, output_tokens: 300 },
  estimated_cost_usd: 0.012,
  ungrounded: [],
};

// parserUrl is read once at import, so each test imports a fresh module.
async function load(url = URL) {
  vi.stubEnv("NEXT_PUBLIC_PARSER_URL", url);
  vi.resetModules();
  return import("@/lib/parser");
}

function respond(status: number, body: unknown) {
  return vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(new Response(JSON.stringify(body), { status }));
}

beforeEach(() => vi.unstubAllEnvs());
afterEach(() => vi.unstubAllEnvs());

describe("parseAgenda", () => {
  it("posts the text with the bearer token, today and time zone", async () => {
    const { parseAgenda } = await load();
    const fetch = respond(200, answer);

    const result = await parseAgenda("Sprint planning Monday", {
      accessToken: "tok",
    });

    expect(result.agenda.title).toBe("Sprint planning");
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe("https://parser.example.test"); // trailing slash trimmed
    expect(init?.method).toBe("POST");
    expect((init?.headers as Record<string, string>).authorization).toBe(
      "Bearer tok",
    );
    const body = JSON.parse(init?.body as string);
    expect(body.text).toBe("Sprint planning Monday");
    expect(body.today).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(typeof body.tz).toBe("string");
  });

  it.each([
    [401, "sign in again"],
    [413, "longer than 10,000"],
    [429, "busy"],
    [504, "too long"],
  ])("explains a %i in plain words", async (status, words) => {
    const { parseAgenda, ParserError } = await load();
    respond(status, { detail: "raw" });

    const error = await parseAgenda("x", { accessToken: "t" }).catch((e) => e);
    expect(error).toBeInstanceOf(ParserError);
    expect(error.status).toBe(status);
    expect(error.message).toContain(words);
  });

  it("passes the server's detail through for other errors", async () => {
    const { parseAgenda } = await load();
    respond(422, { detail: "The model declined to parse this text." });

    await expect(parseAgenda("x", { accessToken: "t" })).rejects.toThrow(
      "The model declined to parse this text.",
    );
  });

  it("rejects an answer that does not match the schema", async () => {
    const { parseAgenda } = await load();
    respond(200, { ...answer, agenda: { title: 42 } });

    await expect(parseAgenda("x", { accessToken: "t" })).rejects.toMatchObject({
      status: 502,
    });
  });

  it("reports an unreachable parser", async () => {
    const { parseAgenda } = await load();
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("offline"));

    await expect(parseAgenda("x", { accessToken: "t" })).rejects.toThrow(
      "Could not reach",
    );
  });

  it("rethrows a cancellation as AbortError", async () => {
    const { parseAgenda } = await load();
    vi.spyOn(globalThis, "fetch").mockRejectedValue(
      new DOMException("aborted", "AbortError"),
    );

    await expect(parseAgenda("x", { accessToken: "t" })).rejects.toMatchObject({
      name: "AbortError",
    });
  });

  it("refuses to call anything when no URL is configured", async () => {
    const { parseAgenda, parserConfigured } = await load("");
    const fetch = vi.spyOn(globalThis, "fetch");

    expect(parserConfigured).toBe(false);
    await expect(parseAgenda("x", { accessToken: "t" })).rejects.toThrow(
      "not configured",
    );
    expect(fetch).not.toHaveBeenCalled();
  });
});
