import { z } from "zod";

/** Browser code must reach the API through the published port; server components
 *  resolve the Compose service name instead. */
export function apiBaseUrl(): string {
  if (typeof window === "undefined") {
    return process.env.INTERNAL_API_URL ?? "http://backend:8000";
  }
  return process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(
  path: string,
  schema: z.ZodType<T>,
  init?: RequestInit,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}${path}`, {
      ...init,
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
      },
    });
  } catch {
    throw new ApiError(0, "Could not reach the API");
  }

  if (response.status === 401) {
    throw new ApiError(401, "Your session has expired - sign in again.");
  }
  if (!response.ok) {
    const detail = await response
      .json()
      .then((body) => errorDetail(body?.detail))
      .catch(() => null);
    throw new ApiError(
      response.status,
      detail ?? `Request failed (${response.status})`,
    );
  }

  if (response.status === 204) {
    return schema.parse(undefined);
  }
  return schema.parse(await response.json());
}

/** FastAPI sends a string, or for 422 a list of {loc, msg}: show the first message. */
function errorDetail(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && typeof detail[0]?.msg === "string") {
    const where = Array.isArray(detail[0].loc)
      ? detail[0].loc.filter((part: unknown) => part !== "body").join(".")
      : "";
    return where ? `${where}: ${detail[0].msg}` : detail[0].msg;
  }
  return null;
}

/* --- schemas mirroring the API contract in README section 5 --- */

export const itemStatuses = ["todo", "in_progress", "done"] as const;
export const itemStatusSchema = z.enum(itemStatuses);

export const itemSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string().nullable(),
  status: itemStatusSchema,
  created_at: z.string(),
  updated_at: z.string(),
});

export const itemListSchema = z.object({
  items: z.array(itemSchema),
  total: z.number().int(),
});

export const healthSchema = z.object({
  status: z.string(),
  database: z.string(),
});

export const itemInputSchema = z.object({
  name: z.string().min(1, "Name is required").max(120, "Name is too long"),
  description: z.string().max(2000, "Description is too long").optional(),
  status: itemStatusSchema,
});

export type ItemStatus = z.infer<typeof itemStatusSchema>;
export type Item = z.infer<typeof itemSchema>;
export type ItemList = z.infer<typeof itemListSchema>;
export type Health = z.infer<typeof healthSchema>;
export type ItemInput = z.infer<typeof itemInputSchema>;

export const agendaItemSchema = z.object({
  title: z.string(),
  owner: z.string().nullable(),
  duration_minutes: z.number().int().nullable(),
});

export const actionItemSchema = z.object({
  description: z.string(),
  owner: z.string().nullable(),
  due_date: z.string().nullable(),
});

export const meetingSchema = z.object({
  id: z.string(),
  title: z.string(),
  organiser: z.string(),
  starts_at: z.string(),
  ends_at: z.string(),
  created_at: z.string(),
  participants: z.array(z.string()),
  agenda_items: z.array(agendaItemSchema),
  action_items: z.array(actionItemSchema),
  ai_model: z.string().nullable(),
  ai_prompt_version: z.string().nullable(),
  ai_suggestion: z.record(z.string(), z.unknown()).nullable(),
  created_by: z.string().nullable(),
});

export const meetingListSchema = z.object({
  meetings: z.array(meetingSchema),
  total: z.number().int(),
});

export type Meeting = z.infer<typeof meetingSchema>;
export type MeetingList = z.infer<typeof meetingListSchema>;

/** What POST /api/v1/meetings accepts; the API validates it again. */
export type MeetingCreate = {
  title: string;
  organiser: string;
  starts_at: string;
  ends_at: string;
  participants: string[];
  agenda_items: {
    title: string;
    owner: string | null;
    duration_minutes: number | null;
  }[];
  action_items: {
    description: string;
    owner: string | null;
    due_date: string | null;
  }[];
  /** The parser's untouched suggestion, kept with the saved meeting. */
  ai?: {
    suggestion: Record<string, unknown>;
    model: string;
    prompt_version: string;
  };
};

/* --- endpoints --- */

/** Meetings need the signed-in user's Cognito access token; items do not. */
function bearer(accessToken: string): HeadersInit {
  return { Authorization: `Bearer ${accessToken}` };
}

export const api = {
  readiness: () => request("/api/v1/health/ready", healthSchema),

  listItems: (params: { limit?: number; offset?: number } = {}) => {
    const query = new URLSearchParams();
    if (params.limit !== undefined) query.set("limit", String(params.limit));
    if (params.offset !== undefined) query.set("offset", String(params.offset));
    const suffix = query.size > 0 ? `?${query}` : "";
    return request(`/api/v1/items${suffix}`, itemListSchema);
  },

  createItem: (payload: ItemInput) =>
    request("/api/v1/items", itemSchema, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  updateItem: (id: string, payload: Partial<ItemInput>) =>
    request(`/api/v1/items/${id}`, itemSchema, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  deleteItem: (id: string) =>
    request(`/api/v1/items/${id}`, z.undefined(), { method: "DELETE" }),

  listMeetings: (accessToken: string, params: { limit?: number } = {}) =>
    request(
      `/api/v1/meetings${params.limit ? `?limit=${params.limit}` : ""}`,
      meetingListSchema,
      { headers: bearer(accessToken) },
    ),

  createMeeting: (accessToken: string, payload: MeetingCreate) =>
    request("/api/v1/meetings", meetingSchema, {
      method: "POST",
      headers: bearer(accessToken),
      body: JSON.stringify(payload),
    }),
};
