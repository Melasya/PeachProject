COMPOSE := docker compose

.PHONY: help up down build logs ps migrate revision test test-backend test-frontend lint fmt clean seed-meetings shell-backend shell-db deploy-backend destroy-backend logs-backend migrate-backend cert domain deploy-frontend destroy-frontend deploy-auth deploy-reports deploy-ai parse llm-hello report-now reports-ls reports-logs reports-queues github-role

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

up: ## Start the whole stack (detached)
	$(COMPOSE) up -d --build

down: ## Stop the stack
	$(COMPOSE) down

clean: ## Stop the stack and wipe the database volume
	$(COMPOSE) down -v

build: ## Rebuild images
	$(COMPOSE) build

logs: ## Tail all logs
	$(COMPOSE) logs -f

ps: ## Show service status
	$(COMPOSE) ps

migrate: ## Apply migrations
	$(COMPOSE) exec backend alembic upgrade head

revision: ## Autogenerate a migration: make revision m="add x"
	$(COMPOSE) exec backend alembic revision --autogenerate -m "$(m)"

test: test-backend test-frontend ## Run all tests

test-backend: ## Run backend tests
	$(COMPOSE) exec backend pytest

test-frontend: ## Run frontend tests
	$(COMPOSE) exec frontend pnpm test

lint: ## Lint both sides
	$(COMPOSE) exec backend ruff check .
	$(COMPOSE) exec frontend pnpm lint

fmt: ## Format both sides
	$(COMPOSE) exec backend ruff format .
	$(COMPOSE) exec frontend pnpm format

seed-meetings: ## Insert demo meetings into the LOCAL Compose database: make seed-meetings week=2026-W40
	$(COMPOSE) exec backend python -m app.reports.seed --week "$(week)" --yes

shell-backend: ## Shell into the backend container
	$(COMPOSE) exec backend bash

shell-db: ## psql into the database
	$(COMPOSE) exec db psql -U $${POSTGRES_USER:-peach} -d $${POSTGRES_DB:-peach}

deploy-backend: ## Build + push the image, roll the Lambda (function URL + RDS PostgreSQL), migrate, write BACKEND_URL to .env
	./scripts/deploy-backend.sh

destroy-backend: ## Delete the backend stack, RDS database included
	./scripts/destroy-backend.sh

logs-backend: ## Tail the deployed backend's CloudWatch logs
	aws logs tail /aws/lambda/$${PROJECT_NAME:-peach}-backend --follow --since 10m

migrate-backend: ## Re-run migrations on the deployed backend (deploy-backend already does)
	aws lambda invoke --function-name $${PROJECT_NAME:-peach}-backend \
		--cli-binary-format raw-in-base64-out --payload '{"action":"migrate"}' /dev/stdout

cert: ## Request + DNS-validate a us-east-1 certificate for the frontend: make cert DOMAIN=app.example.com
	./scripts/domain-frontend.sh cert

domain: ## Assign a custom domain to the frontend: make domain DOMAIN=app.example.com
	./scripts/domain-frontend.sh domain

deploy-frontend: ## Build the static export against BACKEND_URL and ship it to S3 + CloudFront
	./scripts/deploy-frontend.sh

destroy-frontend: ## Delete the frontend stack (bucket + distribution)
	./scripts/destroy-frontend.sh

deploy-auth: ## Create/update the Cognito sign-in stack (user pool, Google, managed login); writes COGNITO_* to .env
	./scripts/deploy-auth.sh

deploy-reports: ## Create/update the weekly-report stack (scheduler -> SQS -> builder -> S3 -> mailer -> SES)
	./scripts/deploy-reports.sh

report-now: ## Queue a report request on SQS: make report-now WEEK=2026-W39 (no WEEK: last complete week)
	./scripts/reports.sh now "$(WEEK)"

reports-ls: ## List reports/ in the reports bucket
	./scripts/reports.sh ls

reports-logs: ## Trigger lines (schedule / sqs / s3) from the builder and mailer logs, last 24h
	./scripts/reports.sh evidence

reports-queues: ## Messages waiting / in flight on the report queue and its DLQ
	./scripts/reports.sh queues

deploy-ai: ## Create/update the agenda-parser stack (Lambda outside the VPC -> Gemini API); writes PARSER_URL to .env
	./scripts/deploy-ai.sh

# Both call the Gemini API with GEMINI_API_KEY from .env (free on the free tier); DRY_RUN=1
# prints the request instead and calls nothing. .env is sourced in this shell and only that
# one variable is handed to the container, by name - its value is never on a command line.
LOAD_KEY := set -a; [ -f .env ] && . ./.env; set +a;

parse: ## Parse an agenda file locally: make parse FILE=app/ai/examples/sample-agenda.txt [DRY_RUN=1]
	@$(LOAD_KEY) $(COMPOSE) run --rm --no-deps -e GEMINI_API_KEY -e PARSER_MODEL -e PARSER_EFFORT backend python -m app.ai.cli parse --file "$(or $(FILE),app/ai/examples/sample-agenda.txt)" $(if $(DRY_RUN),--dry-run)

llm-hello: ## One tiny Gemini call to check the key and model
	@$(LOAD_KEY) $(COMPOSE) run --rm --no-deps -e GEMINI_API_KEY -e PARSER_MODEL backend python -m app.ai.cli hello

github-role: ## Create the IAM role GitHub Actions assumes to deploy (OIDC, no keys)
	./scripts/github-role.sh
