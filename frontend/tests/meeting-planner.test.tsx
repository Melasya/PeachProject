import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MeetingPlanner } from "@/components/meeting-planner";
import { api, type Meeting } from "@/lib/api";
import { parseAgenda, ParserError, type ParseResponse } from "@/lib/parser";
import { renderWithQuery } from "./utils";

type FakeUser = { access_token: string; profile: { sub: string } };
const auth = vi.hoisted(() => ({ user: null as FakeUser | null }));
vi.mock("react-oidc-context", () => ({
  useAuth: () => ({ isAuthenticated: Boolean(auth.user), user: auth.user }),
}));
vi.mock("@/lib/auth", () => ({ authConfigured: true }));
vi.mock("@/lib/parser", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/parser")>()),
  parserConfigured: true,
  parseAgenda: vi.fn(),
}));

const suggestion: ParseResponse = {
  agenda: {
    title: "Sprint planning",
    starts_at: "2026-10-19T07:00:00Z",
    ends_at: "2026-10-19T08:30:00Z",
    organiser: "Olena",
    participants: ["Olena", "Taras"],
    agenda_items: [{ title: "Demo", owner: "Taras", duration_minutes: 15 }],
    action_items: [],
  },
  model: "anthropic.claude-opus-5-5",
  requested_model: "anthropic.claude-opus-5-5",
  prompt_version: "agenda-v1",
  attempts: 1,
  usage: { input_tokens: 900, output_tokens: 300 },
  estimated_cost_usd: 0.012,
  ungrounded: ["Ivan"],
};

const parse = vi.mocked(parseAgenda);

beforeEach(() => {
  auth.user = { access_token: "tok", profile: { sub: "user-1" } };
  parse.mockReset();
  vi.spyOn(api, "listMeetings").mockResolvedValue({ meetings: [], total: 0 });
});

async function pasteAndParse(text = "Sprint planning Monday 10:00") {
  await userEvent.type(screen.getByLabelText("Agenda text"), text);
  await userEvent.click(screen.getByRole("button", { name: "Parse" }));
}

describe("MeetingPlanner", () => {
  it("lists meetings with the signed-in user's access token", async () => {
    renderWithQuery(<MeetingPlanner />);

    expect(await screen.findByText("No meetings yet.")).toBeInTheDocument();
    expect(api.listMeetings).toHaveBeenCalledWith("tok", { limit: 10 });
  });

  it("signed out: never calls the meetings API and cannot save", async () => {
    auth.user = null;
    const create = vi.spyOn(api, "createMeeting");
    renderWithQuery(<MeetingPlanner />);

    expect(screen.getByText("Sign in to see meetings.")).toBeInTheDocument();
    expect(screen.getByText("Sign in to save meetings.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save meeting" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Parse" })).toBeDisabled();
    expect(api.listMeetings).not.toHaveBeenCalled();
    expect(create).not.toHaveBeenCalled();
  });

  it("fills an editable form from the suggestion and saves the edits with provenance", async () => {
    parse.mockResolvedValue(suggestion);
    const create = vi
      .spyOn(api, "createMeeting")
      .mockResolvedValue({ id: "m1" } as unknown as Meeting);
    renderWithQuery(<MeetingPlanner />);

    await pasteAndParse();

    const title = await screen.findByLabelText(/^Title/);
    await waitFor(() => expect(title).toHaveValue("Sprint planning"));
    expect(screen.getByLabelText(/^Organiser/)).toHaveValue("Olena");
    expect(screen.getAllByText("AI suggestion").length).toBeGreaterThan(0);
    expect(screen.getByText(/1 name was left out/)).toBeInTheDocument();
    expect(parse).toHaveBeenCalledWith(
      "Sprint planning Monday 10:00",
      expect.objectContaining({ accessToken: "tok" }),
    );

    await userEvent.clear(title);
    await userEvent.type(title, "Sprint 42 planning");
    await userEvent.click(screen.getByRole("button", { name: "Save meeting" }));

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    const [token, payload] = create.mock.calls[0];
    expect(token).toBe("tok");
    expect(payload.title).toBe("Sprint 42 planning");
    expect(payload.ai).toEqual({
      suggestion: suggestion.agenda,
      model: "anthropic.claude-opus-5-5",
      prompt_version: "agenda-v1",
    });
  });

  it("shows a clear error and leaves the form alone when parsing fails", async () => {
    parse.mockRejectedValue(
      new ParserError(504, "The AI service took too long."),
    );
    renderWithQuery(<MeetingPlanner />);

    await pasteAndParse();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The AI service took too long.",
    );
    expect(screen.getByLabelText(/^Title/)).toHaveValue("");
  });

  it("shows progress and cancels the request", async () => {
    parse.mockImplementation(
      (_text, { signal }) =>
        new Promise((_resolve, reject) =>
          signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          ),
        ),
    );
    renderWithQuery(<MeetingPlanner />);

    await pasteAndParse();
    expect(screen.getByRole("status")).toHaveTextContent("Parsing");

    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(await screen.findByText(/Parsing cancelled/)).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("blocks text over the limit before sending", async () => {
    renderWithQuery(<MeetingPlanner />);
    const box = screen.getByLabelText("Agenda text");

    // Paste rather than type: 10,001 keystrokes would be slow.
    box.focus();
    await userEvent.paste("x".repeat(10_001));

    expect(screen.getByText(/10,001 \/ 10,000/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Parse" })).toBeDisabled();
  });

  it("validates the form before saving without calling the API", async () => {
    const create = vi.spyOn(api, "createMeeting");
    renderWithQuery(<MeetingPlanner />);

    await userEvent.click(screen.getByRole("button", { name: "Save meeting" }));

    expect(await screen.findByText("Title is required")).toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();
  });
});
