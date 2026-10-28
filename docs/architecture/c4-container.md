# C4 level 2: containers

Peach after Lab 6. The grey container (`peach-ai`) is not deployed yet.

```mermaid
C4Container
  title Peach - containers

  Person(user, "Team member", "Plans meetings in a browser")

  System_Ext(google, "Google", "Federated sign-in")
  System_Ext(bedrock, "Amazon Bedrock", "Claude Opus 5.5; refusal fallback Opus 4.8")
  System_Ext(ses, "Amazon SES", "Sends the weekly report")

  Container_Boundary(aws, "AWS account (us-east-1)") {
    Container(spa, "Web app", "Next.js static export on S3 + CloudFront", "Board, meetings, Paste an agenda")
    Container(cognito, "User pool", "Amazon Cognito (peach-auth)", "Email/password and Google; issues access tokens")
    Container(api, "API", "FastAPI on Lambda + Function URL, in VPC (peach-backend)", "Items and meetings; validates every write")
    Container(parser, "Agenda parser", "Python Lambda + Function URL, outside VPC (peach-ai)", "Verifies the token, calls Claude, validates and grounds; never writes")
    ContainerDb(db, "Database", "RDS PostgreSQL db.t4g.micro", "items, meetings, participants, agenda and action items, AI provenance")
    Container(scheduler, "Weekly schedule", "EventBridge Scheduler (peach-reports)", "Monday 07:00 Europe/Kyiv")
    ContainerQueue(queue, "Report requests", "SQS + DLQ", "At-least-once, 3 receives")
    Container(builder, "Report builder", "Lambda in VPC", "Builds the weekly CSV")
    ContainerDb(reports, "Reports bucket", "S3", "reports/<ISO-week>.csv")
    Container(mailer, "Mailer", "Lambda outside VPC", "Emails the CSV")
  }

  Rel(user, spa, "Uses", "HTTPS")
  Rel(spa, cognito, "Signs in", "OIDC + PKCE")
  Rel(cognito, google, "Federates", "OIDC")
  Rel(spa, parser, "POST pasted text + access token", "HTTPS/JSON")
  Rel(parser, cognito, "Fetches JWKS", "HTTPS")
  Rel(parser, bedrock, "Messages API, record_agenda tool", "HTTPS, IAM role")
  Rel(spa, api, "Reads and saves the reviewed meeting, with the access token", "HTTPS/JSON")
  Rel(api, db, "Reads/writes", "SQL")
  Rel(scheduler, queue, "Sends request")
  Rel(queue, builder, "Triggers")
  Rel(builder, db, "Reads meetings", "SQL")
  Rel(builder, reports, "Writes CSV", "S3 gateway endpoint")
  Rel(reports, mailer, "ObjectCreated")
  Rel(mailer, ses, "Sends email")

  UpdateElementStyle(parser, $borderColor="#888888", $fontColor="#000000", $bgColor="#eeeeee")
  UpdateLayoutConfig($c4ShapeInRow="3", $c4BoundaryInRow="1")
```

**What changed in Lab 6:**
- New container: the **Agenda parser**, outside the VPC. Its only outbound calls are to
  Bedrock and the Cognito JWKS.
- New relationship: the web app calls the parser **only** for suggestions, then saves through
  the **API** as before.
- The API now checks the same Cognito access token on every meetings endpoint. It has no
  arrow to Cognito because it never calls it: the signing keys are pinned at deploy time,
  since the API Lambda in the VPC has no internet access.
- The database stores the original suggestion, model and prompt version beside the saved
  meeting.

The parser has no relationship to the database. That absence is deliberate, and a test checks
it (`test_parser_handler.py`). See [ADR 0006](../adr/0006-llm-agenda-parsing.md).
