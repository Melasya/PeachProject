"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus, Sparkles, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useFieldArray, useForm } from "react-hook-form";
import { useAuth } from "react-oidc-context";
import { toast } from "sonner";

import { PageHeader } from "@/components/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Field, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { authConfigured } from "@/lib/auth";
import {
  emptyMeetingForm,
  formToMeeting,
  meetingFormSchema,
  suggestedFields,
  suggestionToForm,
  type MeetingFormValues,
} from "@/lib/meeting-form";
import {
  MAX_AGENDA_CHARS,
  parseAgenda,
  parserConfigured,
  ParserError,
  type ParseResponse,
} from "@/lib/parser";

export function MeetingPlanner() {
  // The suggestion the form was filled from - saved with the meeting as-is.
  const [suggestion, setSuggestion] = useState<ParseResponse | null>(null);
  const [formKey, setFormKey] = useState(0);
  const [initial, setInitial] = useState<MeetingFormValues>(emptyMeetingForm);

  function applySuggestion(result: ParseResponse) {
    setSuggestion(result);
    setInitial(suggestionToForm(result.agenda));
    setFormKey((k) => k + 1);
  }

  function clearForm() {
    setSuggestion(null);
    setInitial(emptyMeetingForm);
    setFormKey((k) => k + 1);
  }

  return (
    <div className="grid gap-10">
      <PageHeader
        icon="🗓️"
        title="Meetings"
        description="Paste an agenda to fill in the form, check every field, then save."
      />
      <AgendaPastePanel onSuggestion={applySuggestion} />
      <MeetingForm
        key={formKey}
        initial={initial}
        suggestion={suggestion}
        onSaved={clearForm}
        onClear={clearForm}
      />
      <RecentMeetings />
    </div>
  );
}

/* --- paste an agenda -------------------------------------------------------- */

function AgendaPastePanel({
  onSuggestion,
}: {
  onSuggestion: (result: ParseResponse) => void;
}) {
  const auth = useAuth();
  const [text, setText] = useState("");
  const [parsing, setParsing] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!parsing) return;
    const timer = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(timer);
  }, [parsing]);

  // Leaving the page cancels a request in flight.
  useEffect(() => () => controller.current?.abort(), []);

  const token = auth.user?.access_token;
  const tooLong = text.length > MAX_AGENDA_CHARS;
  const unavailable = !parserConfigured
    ? "AI parsing is not configured in this build - fill in the form yourself."
    : !authConfigured || !token
      ? "Sign in to use AI parsing. You can still fill in the form yourself."
      : null;

  async function parse() {
    if (!token) return;
    controller.current = new AbortController();
    setElapsed(0);
    setParsing(true);
    setError(null);
    setNotice(null);
    try {
      const result = await parseAgenda(text, {
        accessToken: token,
        signal: controller.current.signal,
      });
      onSuggestion(result);
      const dropped = result.ungrounded.length;
      setNotice(
        `Suggestions from ${result.model} (prompt ${result.prompt_version}) are in the form below - check every field before saving.` +
          (dropped
            ? ` ${dropped} name${dropped === 1 ? " was" : "s were"} left out because the text does not mention ${dropped === 1 ? "it" : "them"}.`
            : ""),
      );
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") {
        setNotice("Parsing cancelled. Nothing was changed.");
      } else if (caught instanceof ParserError) {
        setError(caught.message);
      } else {
        setError("Something went wrong while parsing.");
      }
    } finally {
      setParsing(false);
      controller.current = null;
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Paste an agenda</CardTitle>
        <CardDescription>
          AI reads the text and suggests the fields below. Nothing is saved
          until you review the form and press Save.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <Field data-invalid={tooLong}>
          <FieldLabel htmlFor="agenda-text">Agenda text</FieldLabel>
          <Textarea
            id="agenda-text"
            rows={8}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={
              "Sprint planning - Monday 10:00, 90 min\nAttendees: ..."
            }
            disabled={parsing}
          />
          <p
            className={
              tooLong
                ? "text-xs text-destructive"
                : "text-xs text-muted-foreground"
            }
          >
            {text.length.toLocaleString("en")} /{" "}
            {MAX_AGENDA_CHARS.toLocaleString("en")} characters
          </p>
        </Field>

        {unavailable && (
          <p className="text-sm text-muted-foreground">{unavailable}</p>
        )}

        <div className="flex items-center gap-3">
          <Button
            type="button"
            onClick={() => void parse()}
            disabled={
              Boolean(unavailable) || parsing || !text.trim() || tooLong
            }
          >
            <Sparkles data-icon="inline-start" className="size-4" />
            Parse
          </Button>
          {parsing && (
            <>
              <span
                role="status"
                className="flex items-center gap-2 text-sm text-muted-foreground"
              >
                <Loader2 className="size-4 animate-spin" aria-hidden />
                Parsing... {elapsed}s
              </span>
              <Button
                type="button"
                variant="outline"
                onClick={() => controller.current?.abort()}
              >
                Cancel
              </Button>
            </>
          )}
        </div>

        {error && (
          <Alert variant="destructive">
            <AlertTitle>Could not parse the agenda</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        {notice && <p className="text-sm text-muted-foreground">{notice}</p>}
      </CardContent>
    </Card>
  );
}

/* --- the meeting form ---------------------------------------------------------- */

function AiBadge() {
  return (
    <Badge variant="info" className="ml-2 align-middle">
      AI suggestion
    </Badge>
  );
}

function MeetingForm({
  initial,
  suggestion,
  onSaved,
  onClear,
}: {
  initial: MeetingFormValues;
  suggestion: ParseResponse | null;
  onSaved: () => void;
  onClear: () => void;
}) {
  const queryClient = useQueryClient();
  // The meetings API only accepts signed-in requests.
  const token = useAuth().user?.access_token;
  const form = useForm<MeetingFormValues>({
    resolver: zodResolver(meetingFormSchema),
    defaultValues: initial,
  });
  const agenda = useFieldArray({ control: form.control, name: "agenda_items" });
  const actions = useFieldArray({
    control: form.control,
    name: "action_items",
  });
  const filled = suggestion ? suggestedFields(suggestion.agenda) : new Set();
  const errors = form.formState.errors;

  const mutation = useMutation({
    mutationFn: (values: MeetingFormValues) => {
      if (!token) throw new Error("Sign in to save meetings.");
      return api.createMeeting(token, formToMeeting(values, suggestion));
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["meetings"] });
      toast.success("Meeting saved");
      onSaved();
    },
    onError: (error: Error) => toast.error(`Not saved: ${error.message}`),
  });

  const label = (
    name: keyof MeetingFormValues,
    text: string,
    htmlFor?: string,
  ) => (
    <FieldLabel htmlFor={htmlFor ?? name}>
      {text}
      {filled.has(name) && <AiBadge />}
    </FieldLabel>
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle>Meeting</CardTitle>
        <CardDescription>
          {suggestion
            ? "Filled from your pasted agenda - every field is yours to change."
            : "Fill in the meeting, or paste an agenda above to start from a suggestion."}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="grid gap-5"
          onSubmit={form.handleSubmit((values) => mutation.mutate(values))}
          noValidate
        >
          <Field data-invalid={Boolean(errors.title)}>
            {label("title", "Title")}
            <Input id="title" autoComplete="off" {...form.register("title")} />
            {errors.title && <FieldError errors={[errors.title]} />}
          </Field>

          <Field data-invalid={Boolean(errors.organiser)}>
            {label("organiser", "Organiser")}
            <Input
              id="organiser"
              autoComplete="off"
              {...form.register("organiser")}
            />
            {errors.organiser && <FieldError errors={[errors.organiser]} />}
          </Field>

          <div className="grid gap-5 sm:grid-cols-2">
            <Field data-invalid={Boolean(errors.starts_at)}>
              {label("starts_at", "Starts")}
              <Input
                id="starts_at"
                type="datetime-local"
                {...form.register("starts_at")}
              />
              {errors.starts_at && <FieldError errors={[errors.starts_at]} />}
            </Field>
            <Field data-invalid={Boolean(errors.ends_at)}>
              {label("ends_at", "Ends")}
              <Input
                id="ends_at"
                type="datetime-local"
                {...form.register("ends_at")}
              />
              {errors.ends_at && <FieldError errors={[errors.ends_at]} />}
            </Field>
          </div>

          <Field>
            {label("participants", "Participants (one per line)")}
            <Textarea
              id="participants"
              rows={4}
              {...form.register("participants")}
            />
          </Field>

          <fieldset className="grid gap-3">
            <legend className="mb-2 text-sm font-medium">
              Agenda items
              {filled.has("agenda_items") && <AiBadge />}
            </legend>
            {agenda.fields.map((row, index) => (
              <div
                key={row.id}
                className="grid gap-2 sm:grid-cols-[1fr_10rem_7rem_auto]"
              >
                <Input
                  aria-label={`Agenda item ${index + 1} title`}
                  placeholder="Topic"
                  {...form.register(`agenda_items.${index}.title`)}
                />
                <Input
                  aria-label={`Agenda item ${index + 1} owner`}
                  placeholder="Owner"
                  {...form.register(`agenda_items.${index}.owner`)}
                />
                <Input
                  aria-label={`Agenda item ${index + 1} minutes`}
                  placeholder="Minutes"
                  inputMode="numeric"
                  {...form.register(`agenda_items.${index}.duration_minutes`)}
                />
                <Button
                  type="button"
                  variant="ghost"
                  aria-label={`Remove agenda item ${index + 1}`}
                  onClick={() => agenda.remove(index)}
                >
                  <Trash2 className="size-4" />
                </Button>
                {(errors.agenda_items?.[index]?.title ||
                  errors.agenda_items?.[index]?.duration_minutes) && (
                  <FieldError
                    className="sm:col-span-4"
                    errors={[
                      errors.agenda_items?.[index]?.title,
                      errors.agenda_items?.[index]?.duration_minutes,
                    ]}
                  />
                )}
              </div>
            ))}
            <Button
              type="button"
              variant="outline"
              className="justify-self-start"
              onClick={() =>
                agenda.append({ title: "", owner: "", duration_minutes: "" })
              }
            >
              <Plus data-icon="inline-start" className="size-4" />
              Add agenda item
            </Button>
          </fieldset>

          <fieldset className="grid gap-3">
            <legend className="mb-2 text-sm font-medium">
              Action items
              {filled.has("action_items") && <AiBadge />}
            </legend>
            {actions.fields.map((row, index) => (
              <div
                key={row.id}
                className="grid gap-2 sm:grid-cols-[1fr_10rem_10rem_auto]"
              >
                <Input
                  aria-label={`Action item ${index + 1} description`}
                  placeholder="What needs doing"
                  {...form.register(`action_items.${index}.description`)}
                />
                <Input
                  aria-label={`Action item ${index + 1} owner`}
                  placeholder="Owner"
                  {...form.register(`action_items.${index}.owner`)}
                />
                <Input
                  aria-label={`Action item ${index + 1} due date`}
                  type="date"
                  {...form.register(`action_items.${index}.due_date`)}
                />
                <Button
                  type="button"
                  variant="ghost"
                  aria-label={`Remove action item ${index + 1}`}
                  onClick={() => actions.remove(index)}
                >
                  <Trash2 className="size-4" />
                </Button>
                {errors.action_items?.[index]?.description && (
                  <FieldError
                    className="sm:col-span-4"
                    errors={[errors.action_items?.[index]?.description]}
                  />
                )}
              </div>
            ))}
            <Button
              type="button"
              variant="outline"
              className="justify-self-start"
              onClick={() =>
                actions.append({ description: "", owner: "", due_date: "" })
              }
            >
              <Plus data-icon="inline-start" className="size-4" />
              Add action item
            </Button>
          </fieldset>

          {!token && (
            <p className="text-sm text-muted-foreground">
              Sign in to save meetings.
            </p>
          )}
          <div className="flex gap-2">
            <Button type="submit" disabled={mutation.isPending || !token}>
              {mutation.isPending ? "Saving..." : "Save meeting"}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={onClear}
              disabled={mutation.isPending}
            >
              Clear
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

/* --- recent meetings ------------------------------------------------------------- */

const when = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

function RecentMeetings() {
  const auth = useAuth();
  const token = auth.user?.access_token;
  const { data, isPending, isError, error } = useQuery({
    // Per user, so signing in as someone else never shows a cached list.
    queryKey: ["meetings", auth.user?.profile.sub],
    queryFn: () => api.listMeetings(token!, { limit: 10 }),
    enabled: Boolean(token),
  });

  if (!token) {
    return (
      <section className="grid gap-3" aria-label="Recent meetings">
        <h2 className="font-heading text-lg font-semibold">Recent meetings</h2>
        <p className="text-sm text-muted-foreground">
          Sign in to see meetings.
        </p>
      </section>
    );
  }

  return (
    <section className="grid gap-3" aria-label="Recent meetings">
      <h2 className="font-heading text-lg font-semibold">Recent meetings</h2>
      {isPending && <p className="text-sm text-muted-foreground">Loading...</p>}
      {isError && (
        <p className="text-sm text-muted-foreground">
          Could not load meetings: {error.message}
        </p>
      )}
      {data && data.meetings.length === 0 && (
        <p className="text-sm text-muted-foreground">No meetings yet.</p>
      )}
      <ul className="grid gap-2">
        {data?.meetings.map((m) => (
          <li
            key={m.id}
            className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm"
          >
            <span className="font-medium">{m.title}</span>
            <span className="text-muted-foreground">
              {when.format(new Date(m.starts_at))}
            </span>
            <span className="text-muted-foreground">
              {m.agenda_items.length} agenda · {m.action_items.length} actions ·{" "}
              {m.participants.length} people
            </span>
            {m.ai_model && (
              <Badge
                variant="neutral"
                title={`${m.ai_model} · ${m.ai_prompt_version}`}
              >
                AI-assisted
              </Badge>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
