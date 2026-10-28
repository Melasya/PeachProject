# ADR 0006: Parse pasted agendas with Claude on Amazon Bedrock

- Status: Accepted (implemented locally; not yet deployed)
- Date: 2026-10-17
- Context: Lab 6, "Paste it, check it, save it: an LLM in the product"

## Context

People already have their meeting agendas as text in emails, chats and docs. Typing the same
facts into the meeting form again is slow and error-prone. We want a user to paste that text and
get a filled-in form: title, start and end, organiser, participants, agenda items and action items.

Requirements and constraints:

- The model only **suggests**. A person checks and edits every field, and only the existing,
  validated API writes to the database.
- Missing facts stay empty. A model that invents an owner or a date is worse than no model.
- The pasted text is untrusted. It can contain "ignore your instructions" and similar text.
- The AWS account is on the Free Plan and new. There is no NAT gateway, and the backend Lambda
  runs in the VPC to reach RDS.
- Every call costs money, so it has to be measurable: tokens, latency and an estimated USD cost
  for each request.

## Decision

1. **Model and access.**
   - Claude on Amazon Bedrock, called with the official `anthropic` SDK
     (`AnthropicBedrockMantle`, the Messages API).
   - The default model is `anthropic.claude-opus-5-5` with `effort: low`. On a refusal, the SDK's
     refusal-fallback middleware retries on `anthropic.claude-opus-4-8`.
   - The model, fallback and effort are settings (`PARSER_MODEL`, `PARSER_FALLBACK_MODEL`,
     `PARSER_EFFORT`), so a cheaper model can be tried without a code change.
   - Credentials come from the Lambda execution role: no API keys and no secrets.

2. **Structured output through one tool.**
   - The model must answer by calling `record_agenda`. The tool's JSON schema mirrors the
     Pydantic `ParsedAgenda` model; every property is required and nullable, and
     `additionalProperties` is false.
   - `tool_choice` is `auto` (Opus 5.5 rejects a forced tool while thinking is on). The code
     checks that the tool was actually called.
   - The result is validated with Pydantic. On a validation error the errors go back as an
     `is_error` tool result and the model gets **one** retry. A second failure becomes a 502;
     invalid data is never passed on.

3. **Never invent.**
   - The system prompt says to use `null` or an empty list for anything the text does not state.
   - Code then enforces **grounding**: an organiser, participant or owner whose name does not
     appear in the pasted text is dropped and reported in `ungrounded`, which the UI shows.
   - Relative dates ("next Monday") are resolved against `today` and the user's time zone, which
     the browser sends.

4. **Pasted text is data, not instructions.**
   - The text goes only in the user turn, inside `<pasted_text>` tags. Any such tags inside the
     text are escaped, so it cannot close the block early.
   - The system prompt is fixed, and versioned as `agenda-v1`.
   - Even a successful injection can only produce a suggestion that a person reviews: the parser
     has no tools with side effects and no database access.

5. **A separate parser function.**
   - The parser is a separate Lambda (`infra/ai.yml`, stack `peach-ai`) with its own Function
     URL. It runs the backend image with a different command.
   - It runs **outside the VPC**, so it can reach the Bedrock endpoint without a NAT gateway,
     and it **never imports the database layer** (a test checks this).
   - It verifies the Cognito **access token** itself: RS256 against the user pool's JWKS, with
     the issuer, `client_id`, `token_use` and expiry checked.
   - It rejects text over 10,000 characters with a 413 before calling the model.
   - It maps every failure to a clear HTTP status: 401, 405, 413, 422, 429, 502, 503 or 504.

6. **Observability.**
   - Each request logs one JSON line with the outcome, status, latency, model, requested model,
     prompt version, attempts, input and output tokens, estimated USD cost and character count.
   - The pasted text and the answer are never logged.

7. **Provenance on save.**
   - The meeting gets three new nullable columns (migration `0004`): `ai_suggestion` (JSONB),
     `ai_model` and `ai_prompt_version`. The original suggestion is stored unchanged next to
     what the user actually saved.
   - Agenda and action items are new child tables. Earlier columns and tables, including the
     weekly report's inputs, are unchanged.

8. **The meetings API requires sign-in.**
   - Every `/api/v1/meetings` endpoint, read and write, needs a Cognito access token. Meetings
     hold names and email addresses, and they feed the emailed weekly report.
   - One verifier (`app/auth.py`) serves both the parser and the API: RS256 only, with the
     issuer, expiry, `sub`, `token_use == access` and `client_id` checked. `app/api/auth.py`
     wraps it as a FastAPI dependency.
   - **No internet at runtime.** The API Lambda is in the VPC without a NAT gateway, so it
     cannot fetch the JWKS. `scripts/deploy-backend.sh` fetches the user pool's public keys
     at deploy time, keeps only the RSA public fields, and pins them in `COGNITO_JWKS`.
     Verification is then entirely local. In production, a missing or broken pin answers 503
     and never tries the network. Local development may fetch the keys instead.
   - The new `meetings.created_by` column is the token's `sub`. The request body cannot set a
     user or organisation: `MeetingCreate` forbids extra fields.
   - Items and health stay public, as in Labs 1-5.

## Consequences

**Good:**
- The user stays in control: suggestions are visible, marked "AI suggestion", editable and
  cancellable.
- Missing data stays missing.
- Comparing `ai_suggestion` with the saved fields measures how often people correct the model.
  Together with `prompt_version` and `model`, this makes prompt changes measurable.
- The parser has no database credentials and no network path to RDS, which limits the damage
  of an injection or a bug.
- Cost is visible for each request in CloudWatch Logs.

**Bad / accepted:**
- Two Lambda functions now share one image. The parser is redeployed when the image changes.
- Grounding is a substring check. A nickname, or a name written differently from the text
  (for example "Olena K." for "Olena Kovalenko"), is dropped even though it is right. The user
  re-adds it, which is the safe direction.
- The Function URL uses `AuthType NONE` with the JWT checked in code, because Function URLs only
  support IAM auth. Unauthenticated requests still start the function, but they are rejected
  before any Bedrock call.
- The price table in code is an estimate and has to be updated by hand when prices change.
- **No organisation or tenant isolation.** The app has no organisation model, so meetings are
  not isolated per organisation or per user. Any signed-in user of the pool can list and read
  every meeting; `created_by` records who saved one but does not restrict reading. The pool
  allows self-service sign-up and Google sign-in, so "signed in" means "has an account", not
  "belongs to the team". Real isolation needs an ownership model (organisations, membership,
  scoped queries) and a closed sign-up, both out of scope for this lab.
- `ai_suggestion` is reported by the browser, not signed by the parser. It is reliable for
  measuring corrections by honest users, but a crafted request could store a fake one.
- Pinned keys go stale if Cognito rotates them: tokens signed with a new key get 401 until
  the backend is redeployed. Cognito does not rotate user pool keys on its own today.
- There is no reserved-concurrency cap: new accounts often have a concurrency quota too low to
  reserve any. Cost is bounded by sign-in, the 10,000-character limit, `max_tokens` and the
  single retry.

## Alternatives considered

| Alternative | Why not |
|---|---|
| A parser route in the existing backend Lambda | The backend Lambda is in the VPC. Reaching Bedrock from there needs a NAT gateway or a Bedrock interface endpoint, both paid. It would also give the parser's code path database credentials. |
| Structured outputs (`strict` JSON schema) | Not available on the Bedrock Mantle endpoint. A validated tool call plus one retry gives the same guarantee. |
| Forced `tool_choice` | Rejected by Opus 5.5 with thinking on. |
| Save the parsed result straight to the database | Breaks "a person checks it". Model mistakes would become data. |
| Regex / rule-based parsing | Too brittle for free-form agendas in two languages. |

## Verification

- **Local, with Bedrock mocked:**
  - `backend/tests/test_agenda_parser.py`: request shape, missing fields, grounding, retry on
    each kind of invalid output, refusal, prompt injection, cost.
  - `backend/tests/test_parser_handler.py`: auth, limits, HTTP mapping, logs, no database import.
  - `backend/tests/test_meetings_api.py`: save with provenance, plus validation.
  - `backend/tests/test_meetings_auth.py`, for every meetings endpoint:
    - no token, or each of 14 kinds of invalid token, gets a 401 and writes nothing;
    - a valid token works, and `created_by` comes from it;
    - the client cannot set an owner;
    - bad configuration fails closed with a 503, without touching the network;
    - items and health are unchanged.
  - Frontend: `tests/parser.test.ts`, `tests/meeting-form.test.ts` and
    `tests/meeting-planner.test.tsx`.
- **Against AWS** (after account verification): `make llm-hello`, `make parse`, then the five
  lab tests in the README, section 16.
