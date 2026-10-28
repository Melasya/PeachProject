import { z } from "zod";

import type { MeetingCreate } from "@/lib/api";
import type { ParsedAgenda, ParseResponse } from "@/lib/parser";

/* The meeting form works in strings - what inputs hold - and converts to the
 * API's shape on save. The API validates everything again. */

export const meetingFormSchema = z
  .object({
    title: z
      .string()
      .trim()
      .min(1, "Title is required")
      .max(200, "Title is too long"),
    organiser: z
      .string()
      .trim()
      .min(1, "Organiser is required")
      .max(254, "Organiser is too long"),
    starts_at: z.string().min(1, "Start time is required"),
    ends_at: z.string().min(1, "End time is required"),
    participants: z.string(),
    agenda_items: z.array(
      z.object({
        title: z
          .string()
          .trim()
          .min(1, "Give the item a title")
          .max(200, "Too long"),
        owner: z.string().max(120, "Too long"),
        duration_minutes: z
          .string()
          .regex(/^(\d{1,3})?$/, "Minutes as a whole number"),
      }),
    ),
    action_items: z.array(
      z.object({
        description: z
          .string()
          .trim()
          .min(1, "Describe the action")
          .max(500, "Too long"),
        owner: z.string().max(120, "Too long"),
        due_date: z.string(),
      }),
    ),
  })
  .refine(
    (v) =>
      !v.starts_at || !v.ends_at || new Date(v.ends_at) > new Date(v.starts_at),
    { path: ["ends_at"], message: "End must be after the start" },
  );

export type MeetingFormValues = z.infer<typeof meetingFormSchema>;

export const emptyMeetingForm: MeetingFormValues = {
  title: "",
  organiser: "",
  starts_at: "",
  ends_at: "",
  participants: "",
  agenda_items: [],
  action_items: [],
};

const pad = (n: number) => String(n).padStart(2, "0");

/** ISO instant -> the browser-local value a datetime-local input holds. */
export function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** A suggestion as editable form values: missing facts stay empty, never guessed. */
export function suggestionToForm(agenda: ParsedAgenda): MeetingFormValues {
  return {
    title: agenda.title ?? "",
    organiser: agenda.organiser ?? "",
    starts_at: toLocalInput(agenda.starts_at),
    ends_at: toLocalInput(agenda.ends_at),
    participants: agenda.participants.join("\n"),
    agenda_items: agenda.agenda_items.map((item) => ({
      title: item.title,
      owner: item.owner ?? "",
      duration_minutes:
        item.duration_minutes === null ? "" : String(item.duration_minutes),
    })),
    action_items: agenda.action_items.map((item) => ({
      description: item.description,
      owner: item.owner ?? "",
      due_date: item.due_date ?? "",
    })),
  };
}

/** Which form fields the suggestion actually filled - those get a badge. */
export function suggestedFields(
  agenda: ParsedAgenda,
): Set<keyof MeetingFormValues> {
  const filled = new Set<keyof MeetingFormValues>();
  if (agenda.title) filled.add("title");
  if (agenda.organiser) filled.add("organiser");
  if (agenda.starts_at) filled.add("starts_at");
  if (agenda.ends_at) filled.add("ends_at");
  if (agenda.participants.length) filled.add("participants");
  if (agenda.agenda_items.length) filled.add("agenda_items");
  if (agenda.action_items.length) filled.add("action_items");
  return filled;
}

const orNull = (value: string) => (value.trim() ? value.trim() : null);

/** Form values -> POST /api/v1/meetings, with the untouched suggestion if one was used. */
export function formToMeeting(
  values: MeetingFormValues,
  suggestion: ParseResponse | null,
): MeetingCreate {
  return {
    title: values.title.trim(),
    organiser: values.organiser.trim(),
    // datetime-local is browser-local time; toISOString makes it an instant.
    starts_at: new Date(values.starts_at).toISOString(),
    ends_at: new Date(values.ends_at).toISOString(),
    participants: values.participants
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean),
    agenda_items: values.agenda_items.map((item) => ({
      title: item.title.trim(),
      owner: orNull(item.owner),
      duration_minutes: item.duration_minutes
        ? Number(item.duration_minutes)
        : null,
    })),
    action_items: values.action_items.map((item) => ({
      description: item.description.trim(),
      owner: orNull(item.owner),
      due_date: orNull(item.due_date),
    })),
    ...(suggestion
      ? {
          ai: {
            suggestion: suggestion.agenda,
            model: suggestion.model,
            prompt_version: suggestion.prompt_version,
          },
        }
      : {}),
  };
}
