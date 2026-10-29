# ADR 0007: Call the Gemini API instead of Claude on Amazon Bedrock

- Status: Accepted (implemented and tested locally with a fake client; not yet deployed)
- Date: 2026-10-29
- Context: Lab 6. Supersedes decision 1 of [ADR 0006](0006-llm-agenda-parsing.md).

## Context

ADR 0006 chose Claude on Amazon Bedrock. That code was deployed with the backend, but this AWS
account cannot run Bedrock inference:

- On 28 October, one test request each (retries off) gave HTTP 403 for Claude Opus 5.5 and
  Opus 4.8, and HTTP 429 "Too many tokens per day" for Nova Micro, with no usage recorded.
- Service Quotas shows every Bedrock inference quota at **0** in us-east-1 and us-west-2,
  even after the account became active. The models are ACTIVE; the quotas block them.

The instructor allowed Google's Gemini API instead. Everything else in ADR 0006 has to stay:
Parse → Edit → Save, the forced structured answer, Pydantic validation with one retry,
grounding, Cognito auth, safe logging, and a parser Lambda that cannot reach the database.

## Decision

1. **Provider and SDK.** Google's Gemini API through the official `google-genai` SDK (pinned
   `>=2,<3`; 2.34.0 in `uv.lock`). `anthropic[bedrock]` is removed.
2. **Model.** `gemini-3.5-flash-lite`, a stable model that supports function calling and
   structured output (checked on ai.google.dev, 2026-10-29), and that `models.list` shows is
   available to our key. `gemini-3.8-flash` was the first choice, but its one test call got 503
   "high demand". `PARSER_MODEL` can name another model. `PARSER_EFFORT` (`low` by default)
   maps to Gemini's thinking level.
3. **Same contract, different wire format.** `record_agenda` becomes a Gemini function
   declaration with the *same* JSON schema (`parameters_json_schema`).
   - Gemini can force the call (`mode: ANY`, `allowed_function_names: [record_agenda]`), which
     Opus 5.5 could not, so a plain-text answer should be rare.
   - The retry sends the model's turn back unchanged (it carries Gemini's thought signatures)
     plus a function response with the validation errors.
   - Safety stops (`SAFETY`, `PROHIBITED_CONTENT`, ...) and blocked prompts are refusals: 422,
     with no retry. `MALFORMED_FUNCTION_CALL` counts as "no call" and gets the one retry.
   - The prompt text did not change, so the prompt version stays `agenda-v1`; the model is
     recorded separately in `ai_model`.
4. **The API key stays on the backend.**
   - Locally: `GEMINI_API_KEY` in `.env`, which git ignores.
   - On Lambda: an SSM Parameter Store **SecureString**, `/peach/gemini-api-key`, created by hand
     with the AWS-managed `aws/ssm` key. The stack gets only its name
     (`GEMINI_API_KEY_PARAMETER`), and the role may `ssm:GetParameter` on that one ARN. The
     function reads it once per container and keeps it in memory.
   - It is never in code, git, CloudFormation parameters, Lambda environment variables, log
     lines, error responses or any `NEXT_PUBLIC_*` variable. The SDK sends it in the
     `x-goog-api-key` header, not the URL. Upstream error messages are never logged or
     returned, and a test checks this with a message that echoes the key.
5. **Error mapping.** 429 → 429. A timeout or upstream 504 → 504. A bad key (400 "API key"),
   401, 403 or 404 → 503. A missing or unreadable key → 503. Anything else → 502.
6. **Transport retry.** One retry on 408/429/5xx and connection errors (`HttpRetryOptions`,
   2 attempts), as before. Timeout 45 s per call.
7. **Cost estimate.** Paid-tier prices from ai.google.dev, 2026-10-29: 3.5 Flash-Lite $0.30
   input / $2.50 output per MTok. The table also lists 3.8 Flash ($0.75 / $3.75 until
   2026-12-31, then $1.50 / $7.50) for anyone who switches. Thinking tokens count as output.
   The free tier costs nothing, so the logged cost is an upper estimate.

## Why SSM and not Secrets Manager or a plain environment variable

- **A plain Lambda environment variable:** anyone who can read the function's configuration can
  read it, and it would sit in the CloudFormation parameters too.
- **Secrets Manager:** just as secure here, but $0.40 per secret per month. We need no
  rotation, and the plan is to create nothing paid that is not needed.
- **SSM SecureString:** a standard parameter is free and KMS-encrypted. The parser already runs
  outside the VPC, so it reaches SSM without a NAT gateway or an endpoint.

## Consequences

- **AWS:** the Bedrock permission is gone, and one free SSM parameter is added. `peach-ai`
  (Lambda, function URL, log group) is still created only by `make deploy-ai`. Nothing hourly.
- **Billing:** Gemini usage is billed by Google, not AWS. The free tier has low rate limits,
  which show up as 429s.
- **Privacy:** on Gemini's free tier, Google may use what is sent to improve its products. Use
  only test agendas (fictional names, `@example.com`); real personal data needs the paid tier.
  The privacy page should say that pasted text goes to Google.
- **Data leaves AWS.** Pasted text now goes to Google instead of staying with an AWS service.
- **Not yet confirmed live:** that Gemini accepts the schema's `anyOf: [..., null]` exactly as
  written, and how well it extracts. The first real call (`make llm-hello`, then the five lab
  cases) checks both.
- **Switching back:** if Bedrock quotas are restored, going back means reverting this change;
  the Bedrock version is in git history (commit b59b76f). There is no runtime provider switch.
