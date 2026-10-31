import { z } from "zod";

/* The agenda parser: a separate function URL (infra/ai.tf), compiled in at
 * build time like the API URL. Empty means AI parsing is off in this build. */
export const parserUrl = (process.env.NEXT_PUBLIC_PARSER_URL ?? "").replace(
  /\/$/,
  "",
);
export const parserConfigured = Boolean(parserUrl);

/** Mirrors the parser's own limit, so the form can say so before sending. */
export const MAX_AGENDA_CHARS = 10_000;

export const parsedAgendaSchema = z.object({
  title: z.string().nullable(),
  starts_at: z.string().nullable(),
  ends_at: z.string().nullable(),
  organiser: z.string().nullable(),
  participants: z.array(z.string()),
  agenda_items: z.array(
    z.object({
      title: z.string(),
      owner: z.string().nullable(),
      duration_minutes: z.number().int().nullable(),
    }),
  ),
  action_items: z.array(
    z.object({
      description: z.string(),
      owner: z.string().nullable(),
      due_date: z.string().nullable(),
    }),
  ),
});

export const parseResponseSchema = z.object({
  agenda: parsedAgendaSchema,
  model: z.string(),
  requested_model: z.string(),
  prompt_version: z.string(),
  attempts: z.number().int(),
  usage: z.object({
    input_tokens: z.number().int(),
    output_tokens: z.number().int(),
  }),
  estimated_cost_usd: z.number().nullable(),
  ungrounded: z.array(z.string()),
});

export type ParsedAgenda = z.infer<typeof parsedAgendaSchema>;
export type ParseResponse = z.infer<typeof parseResponseSchema>;

export class ParserError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ParserError";
  }
}

const MESSAGES: Record<number, string> = {
  401: "Your session has expired - sign in again to use AI parsing.",
  413: `The text is longer than ${MAX_AGENDA_CHARS.toLocaleString("en")} characters.`,
  429: "The AI service is busy. Wait a moment and try again.",
  504: "The AI service took too long. Try again, or fill in the form yourself.",
};

function localToday(): string {
  // en-CA formats as YYYY-MM-DD in the browser's own time zone.
  return new Intl.DateTimeFormat("en-CA").format(new Date());
}

/** Ask the parser for suggestions. Throws ParserError, or AbortError if cancelled. */
export async function parseAgenda(
  text: string,
  options: { accessToken: string; signal?: AbortSignal },
): Promise<ParseResponse> {
  if (!parserConfigured) {
    throw new ParserError(0, "AI parsing is not configured in this build.");
  }
  let response: Response;
  try {
    response = await fetch(parserUrl, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${options.accessToken}`,
      },
      body: JSON.stringify({
        text,
        today: localToday(),
        tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
      }),
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new ParserError(0, "Could not reach the AI parser.");
  }

  if (!response.ok) {
    const detail = await response
      .json()
      .then((body) => (typeof body?.detail === "string" ? body.detail : null))
      .catch(() => null);
    throw new ParserError(
      response.status,
      MESSAGES[response.status] ??
        detail ??
        `Parsing failed (${response.status}).`,
    );
  }

  const parsed = parseResponseSchema.safeParse(await response.json());
  if (!parsed.success) {
    throw new ParserError(502, "The AI parser sent an unexpected answer.");
  }
  return parsed.data;
}
