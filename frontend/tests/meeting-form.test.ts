import { describe, expect, it } from "vitest";

import {
  emptyMeetingForm,
  formToMeeting,
  meetingFormSchema,
  suggestedFields,
  suggestionToForm,
  toLocalInput,
} from "@/lib/meeting-form";
import type { ParseResponse } from "@/lib/parser";

const agenda: ParseResponse["agenda"] = {
  title: "Sprint planning",
  starts_at: "2026-10-19T07:00:00Z",
  ends_at: "2026-10-19T08:30:00Z",
  organiser: null,
  participants: ["Olena", "Taras"],
  agenda_items: [
    { title: "Demo", owner: "Taras", duration_minutes: 15 },
    { title: "Backlog", owner: null, duration_minutes: null },
  ],
  action_items: [{ description: "Book a room", owner: null, due_date: null }],
};

const suggestion: ParseResponse = {
  agenda,
  model: "anthropic.claude-opus-5-5",
  requested_model: "anthropic.claude-opus-5-5",
  prompt_version: "agenda-v1",
  attempts: 1,
  usage: { input_tokens: 1, output_tokens: 1 },
  estimated_cost_usd: null,
  ungrounded: [],
};

describe("suggestionToForm", () => {
  it("leaves missing facts empty instead of guessing", () => {
    const values = suggestionToForm(agenda);

    expect(values.organiser).toBe("");
    expect(values.agenda_items[1]).toEqual({
      title: "Backlog",
      owner: "",
      duration_minutes: "",
    });
    expect(values.action_items[0].due_date).toBe("");
    expect(values.participants).toBe("Olena\nTaras");
  });

  it("converts instants to datetime-local values and back", () => {
    const local = toLocalInput(agenda.starts_at);
    expect(local).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);
    expect(new Date(local).toISOString()).toBe("2026-10-19T07:00:00.000Z");
    expect(toLocalInput(null)).toBe("");
    expect(toLocalInput("not a date")).toBe("");
  });

  it("marks only the fields the suggestion filled", () => {
    const filled = suggestedFields(agenda);
    expect(filled.has("title")).toBe(true);
    expect(filled.has("organiser")).toBe(false);
    expect(filled.has("agenda_items")).toBe(true);
  });
});

describe("meetingFormSchema", () => {
  it("requires a title, organiser and both times", () => {
    const result = meetingFormSchema.safeParse(emptyMeetingForm);
    expect(result.success).toBe(false);
    const paths = result.error!.issues.map((i) => i.path.join("."));
    expect(paths).toEqual(
      expect.arrayContaining(["title", "organiser", "starts_at", "ends_at"]),
    );
  });

  it("rejects an end before the start and non-numeric minutes", () => {
    const values = {
      ...suggestionToForm(agenda),
      organiser: "Olena",
      ends_at: toLocalInput("2026-10-19T06:00:00Z"),
    };
    values.agenda_items[0].duration_minutes = "ten";

    const result = meetingFormSchema.safeParse(values);
    const paths = result.error!.issues.map((i) => i.path.join("."));
    expect(paths).toEqual(
      expect.arrayContaining(["ends_at", "agenda_items.0.duration_minutes"]),
    );
  });
});

describe("formToMeeting", () => {
  it("sends the edited values plus the untouched suggestion", () => {
    const values = { ...suggestionToForm(agenda), organiser: "Olena" };
    values.title = "  Sprint 42 planning ";
    values.participants = "Olena\n\n Taras \nIvan";

    const payload = formToMeeting(values, suggestion);

    expect(payload.title).toBe("Sprint 42 planning");
    expect(payload.starts_at).toBe("2026-10-19T07:00:00.000Z");
    expect(payload.participants).toEqual(["Olena", "Taras", "Ivan"]);
    expect(payload.agenda_items).toEqual([
      { title: "Demo", owner: "Taras", duration_minutes: 15 },
      { title: "Backlog", owner: null, duration_minutes: null },
    ]);
    expect(payload.action_items[0]).toEqual({
      description: "Book a room",
      owner: null,
      due_date: null,
    });
    // Provenance is the original suggestion, not the edited form.
    expect(payload.ai).toEqual({
      suggestion: agenda,
      model: "anthropic.claude-opus-5-5",
      prompt_version: "agenda-v1",
    });
  });

  it("sends no provenance for a meeting typed by hand", () => {
    const values = {
      ...emptyMeetingForm,
      title: "1:1",
      organiser: "Olena",
      starts_at: "2026-10-19T10:00",
      ends_at: "2026-10-19T10:30",
    };
    expect(formToMeeting(values, null)).not.toHaveProperty("ai");
  });
});
