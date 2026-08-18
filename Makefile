# The front door. `make` with no target prints the list below.
#
# Every target is one word and asks for whatever it needs. Nothing here
# requires remembering a flag — the flags still exist underneath, on
# `uv run wealth ...`, for CI and scripting.

.DEFAULT_GOAL := help
.PHONY: help setup doctor menu run report demo compare judge cost trace test clean

RUN := uv run

help:  ## Show this list
	@printf '\n  \033[1mwealth-deep-agent\033[0m — a grounded personal wealth agent\n'
	@printf '  \033[2mReads a brokerage account, six months of card spending, and an investment\n'
	@printf '  policy. Tells you what to change, and shows where every number came from.\033[0m\n\n'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / \
		{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@printf '\n  \033[2mNew here? Run\033[0m make menu\n\n'

setup:  ## Install dependencies and check your API keys
	uv sync
	@$(MAKE) --no-print-directory doctor

doctor:  ## Pre-flight check — run this before you present
	@$(RUN) wealth doctor

menu:  ## Not sure what you want? Start here.
	@$(RUN) wealth menu

run:  ## Run a wealth review and open the report
	@$(RUN) wealth menu

report:  ## Open a report from a previous run
	@$(RUN) wealth report

demo:  ## The workshop walkthrough, one keypress per beat
	@$(RUN) wealth demo

compare:  ## naive vs baseline vs verified, side by side (free, offline)
	@$(RUN) wealth compare

judge:  ## Loop 0 — measure the judge against human labels
	@$(RUN) wealth evals self-test

cost:  ## What did the last run cost?
	@$(RUN) wealth cost

trace:  ## Open this project in LangSmith
	@open "https://smith.langchain.com/o/me/projects/p/$${LANGSMITH_PROJECT:-wealth-deep-agent}" \
		2>/dev/null || printf '  Set LANGSMITH_PROJECT, then open smith.langchain.com\n'

test:  ## Run the test suite
	@$(RUN) pytest -q

clean:  ## Remove old run directories (keeps the frozen demo artifacts)
	@n=$$(ls -1 runs 2>/dev/null | wc -l | tr -d ' '); \
	 rm -rf runs; printf '  removed %s run director%s\n' "$$n" "$$([ "$$n" = 1 ] && echo y || echo ies)"
