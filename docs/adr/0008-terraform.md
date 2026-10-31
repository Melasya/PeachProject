# ADR 0008: Terraform, with its state in HCP Terraform, for the Lab 4-6 infrastructure

- Status: Accepted
- Date: 2026-10-31
- Context: Lab 7, "The cloud as code: from CloudFormation to Terraform". (The lab text calls
  this ADR 0007; that number was already taken by the Gemini decision.)

## Context

Labs 4-6 added three CloudFormation stacks: `peach-auth` (Cognito), `peach-reports` (the weekly
report) and `peach-ai` (the agenda parser). Each had its own deploy script, run by hand from a
laptop with the developer's credentials. Nothing recorded who ran what, and a plan was optional
(a change set nobody created).

The project already reaches outside AWS - the Google OAuth client, the Gemini API key, GitHub - and
will need a second environment. The stacks hold data that must survive the move: every account
in the user pool and every report in the bucket.

## Decision

1. **Terraform for the three stacks**, in one root module, `infra/`: `auth.tf`, `reports.tf`,
   `ai.tf`, `variables.tf`, `outputs.tf`, `versions.tf`. The older stacks - `peach-backend`,
   `peach-frontend`, `peach-github-oidc` - stay CloudFormation for now; nothing in `infra/` reads
   them (their few IDs are in `terraform.tfvars`).
2. **Adopted, not re-created.** The stacks get `DeletionPolicy: Retain` (on the deployed templates,
   nothing else changed) and are deleted, which leaves every resource in place; Terraform then
   adopts all 38 with `import` blocks and their real IDs. Names do not change - the IAM roles keep
   the names CloudFormation generated, and the Lambda permissions keep their statement IDs. The
   import plan: 38 to import, 0 to add, 0 to destroy. Its 2 in-place updates change no value; they
   only mark two already-equal values as sensitive (the Google client secret and the builder's
   database URL), which the plan's JSON confirms.
3. **State in HCP Terraform**, workspace `peach-dev`, remote execution, connected to the GitHub
   repository with `infra` as the working directory. A pull request gets a speculative plan as a
   check; a merge to `main` applies. The Makefile has `infra-plan` and no apply.
4. **No AWS keys in HCP Terraform.** Runs use dynamic credentials: an IAM OIDC provider trusts
   `app.terraform.io`, and two roles - one for plans, one for applies - trust only workspace
   `peach-dev` and only their own run phase. The plan role is read-only; the apply role can write
   only `peach-*` resources of the kinds `infra/` manages. Both are created by `infra/bootstrap/`
   (local, gitignored state), applied once by a person.
5. **Code versus configuration.** Terraform owns each function's configuration; the code (the
   backend image for the parser and builder, the mailer zip) is the application's, shipped by
   `make deploy-functions`. Terraform ignores `image_uri` and the zip location.

## Alternatives

- **Stay with CloudFormation.** AWS keeps the state and rolls back failures, which is real value.
  But it covers only AWS, and every change still started from a laptop. Free, yet the workflow -
  plan, review, apply by a machine - would have to be built around it.
- **AWS CDK.** Code instead of YAML, but it compiles to CloudFormation: the same AWS-only reach,
  and a Node toolchain on top.
- **OpenTofu.** The same language and the same files under an open licence (MPL), and a real
  option. We chose Terraform because HCP Terraform - state, locking, VCS runs and dynamic AWS
  credentials in one free service - is what the course uses. Moving to OpenTofu later means
  changing the backend, not the code.
- **State in S3 with a lock file.** Free and fully ours, but it needs its own bootstrap and does
  not run plans on pull requests by itself.

## Consequences

**Who can change the infrastructure.** Anyone who can open a pull request can propose a change;
a reviewer reads the plan in the check; merging to `main` applies it, with the apply role. Nobody
needs AWS keys for that. `terraform apply` from a laptop against the workspace is for emergencies
only, and the workspace setting can forbid it.

**Where the secrets end up.**
- *Google client secret:* a sensitive workspace variable, and therefore in the state. Plans print
  `(sensitive value)`, but the state holds it in clear text: anyone who can read the workspace
  state can read it.
- *Database URL:* read at plan time from the Secrets Manager secret `peach/backend/database-url`
  and placed in the builder's environment, so it is in the state too.
- *Gemini API key:* not in Terraform at all. Terraform knows only the SSM parameter's name.
- HCP Terraform encrypts the state at rest. Access is limited to the organization's members; the
  state is never downloaded to a laptop or committed. `.gitignore` excludes `*.tfstate`,
  `*.tfplan`, `.terraform/` and override files.

**If HCP Terraform is down.** Nothing that runs stops: the app, the functions and the schedule
do not depend on it. Infrastructure changes wait. In an emergency a person can switch the workspace
to local execution and apply from a laptop with their own credentials - the state stays in HCP
Terraform, so it must be reachable. If it is not, a person can still change AWS by hand and bring
the code into line afterwards; the next plan shows the drift.

**Other consequences.**
- Every resource the old templates created is now in `infra/*.tf` with the same name. Changing
  `aws_cognito_user_pool.main`'s `name`, `username_attributes` or `alias_attributes`, or
  `aws_s3_bucket.reports`'s `bucket`, forces replacement: a new, empty user pool (every account
  gone) or bucket (every report gone). Never apply a plan with `-/+` on either.
- Drift is visible: every plan compares the code with AWS. A console fix must become a pull
  request, or the next apply removes it.
- CI cannot read the state, so four public values (Cognito authority, client ID and domain, the
  parser URL) are copied into GitHub repository variables. If one changes, the variable must be
  updated - a deliberate, small duplication.
- The Lab 5 cycle is gone: bucket, invoke permission and notification are three resources, ordered
  by references.
