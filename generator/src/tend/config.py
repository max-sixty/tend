"""Read and validate .config/tend.yaml."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import click
from ruamel.yaml import YAML

# ruamel.yaml parses YAML 1.2 by default, which fixes PyYAML's `on:` → True
# trap and the Norway problem (yes/no/on/off coerced to bool).
_YAML = YAML(typ="safe", pure=True)

KNOWN_WORKFLOWS = {
    "review",
    "mention",
    "triage",
    "ci-fix",
    "nightly",
    "weekly",
    "notifications",
    "review-runs",
}
KNOWN_TOP_LEVEL = {
    "bot_name",
    "harness",
    "model",
    "effort",
    "protected_branches",
    "secrets",
    "setup",
    "workflows",
}
KNOWN_HARNESSES = {"claude", "codex"}
# Claude harness reads claude_token (OAuth) and anthropic_api_key (console.
# anthropic.com) — adopters set one. Codex harness reads openai_key and
# codex_auth_json; the latter is the subscription-funded path
# (~/.codex/auth.json contents stored as a repo secret), officially
# discouraged for public repos but supported.
KNOWN_SECRETS_KEYS = {
    "bot_token",
    "claude_token",
    "anthropic_api_key",
    "openai_key",
    "codex_auth_json",
    "allowed",
}
_GITHUB_USERNAME = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?$")


ALLOWED_STEP_FIELDS = {
    "uses",
    "run",
    "name",
    "id",
    "if",
    "with",
    "env",
    "shell",
    "working-directory",
    "continue-on-error",
    "timeout-minutes",
}
DICT_STEP_FIELDS = {"with", "env"}


@dataclass
class SetupStep:
    """A single project setup step, mirroring GitHub's step schema.

    Exactly one of `uses` or `run`, plus any of `with`, `env`, `name`,
    `id`, `shell`, `working-directory`, `continue-on-error`,
    `timeout-minutes`, `if`. The renderer injects the notifications
    pre-check `if:` guard when absent. For multi-step setup, add multiple
    entries to the `setup:` list — or reference a local composite action
    with `uses`.
    """

    fields: dict


@dataclass
class WorkflowConfig:
    enabled: bool = True
    prompt: str = ""
    cron: str = ""
    watched_workflows: list[str] | None = None
    branches: list[str] | None = None
    workflow_extra: dict | None = None
    jobs: dict[str, dict] | None = None


# Claude model allowlist — the set is small and stable enough that a
# typo-catching gate at config load is worth the maintenance.
# Codex models are NOT enumerated here: Codex's catalog churns
# (gpt-5.1-codex was current at harness bring-up; gone by the next month),
# and a stale allowlist would silently block adopters from picking a newer
# model. We pass any user-supplied string through and let `codex exec` error
# at runtime if it's wrong.
KNOWN_MODELS_BY_HARNESS = {
    "claude": {"opus", "sonnet", "haiku"},
}
DEFAULT_MODEL_BY_HARNESS = {
    "claude": "opus",
    "codex": "gpt-5.5",
}
# Codex `--config model_reasoning_effort=...` values, per the supported
# levels Codex's models_cache advertises for every current model. Claude does
# not use this field. Empty string means "leave at Codex CLI default".
KNOWN_EFFORTS = {"", "low", "medium", "high", "xhigh"}


@dataclass
class Config:
    bot_name: str
    default_branch: str
    protected_branches: list[str]
    bot_token_secret: str
    claude_token_secret: str
    anthropic_api_key_secret: str
    openai_key_secret: str
    codex_auth_json_secret: str
    harness: str
    model: str
    effort: str
    setup: list[SetupStep]
    workflows: dict[str, WorkflowConfig]
    # Owner of the repo where workflows will run. Used to gate jobs that fail
    # noisily on forks (no access to bot/Claude secrets). Not user-configurable;
    # cli.init populates this via `gh repo view` so fork-based maintainer
    # workflows still get the canonical owner. Empty means "skip the guard"
    # (gh unavailable, or no default repo configured).
    repo_owner: str = ""
    allowed_repo_secrets: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        if path is None:
            path = Path(".config/tend.yaml")
        if not path.exists():
            legacy = Path(".config/tend.toml")
            if path == Path(".config/tend.yaml") and legacy.exists():
                raise click.ClickException(
                    f"Found {legacy} but tend now reads {path}. "
                    "Run `uvx tend@latest init` to migrate "
                    "(verifies the parsed config is equivalent, swaps the file, "
                    "and regenerates workflows in one step)."
                )
            raise click.ClickException(f"Config not found: {path}")
        with path.open() as f:
            raw = _YAML.load(f) or {}

        if not isinstance(raw, dict):
            raise click.ClickException(
                f"{path} must contain a YAML mapping at the top level"
            )

        if "bot_name" not in raw:
            raise click.ClickException("Missing required field: bot_name")

        bot_name = raw["bot_name"]
        if not isinstance(bot_name, str) or not bot_name:
            raise click.ClickException("bot_name must not be empty")
        if not _GITHUB_USERNAME.match(bot_name):
            raise click.ClickException(
                f"bot_name '{bot_name}' is not a valid GitHub username "
                "(only letters, digits, and hyphens)"
            )

        harness = raw.get("harness", "claude")
        if harness not in KNOWN_HARNESSES:
            raise click.ClickException(
                f"harness '{harness}' is not recognized "
                f"(known: {', '.join(sorted(KNOWN_HARNESSES))})"
            )

        model = raw.get("model", DEFAULT_MODEL_BY_HARNESS[harness])
        known_models = KNOWN_MODELS_BY_HARNESS.get(harness)
        if known_models is not None and model not in known_models:
            raise click.ClickException(
                f"model '{model}' is not recognized for harness '{harness}' "
                f"(known: {', '.join(sorted(known_models))})"
            )

        effort = raw.get("effort", "")
        if effort not in KNOWN_EFFORTS:
            raise click.ClickException(
                f"effort '{effort}' is not recognized "
                f"(known: {', '.join(sorted(e for e in KNOWN_EFFORTS if e))})"
            )
        if effort and harness != "codex":
            raise click.ClickException(
                f"effort is only valid for harness = 'codex' (got harness = '{harness}')"
            )

        unknown = set(raw.keys()) - KNOWN_TOP_LEVEL
        for key in sorted(unknown):
            click.echo(f"Warning: unknown config key '{key}'", err=True)

        protected_branches = raw.get("protected_branches", [])
        if not isinstance(protected_branches, list) or not all(
            isinstance(b, str) and b for b in protected_branches
        ):
            raise click.ClickException(
                "protected_branches must be a list of non-empty strings"
            )

        secrets = raw.get("secrets", {}) or {}
        unknown_secrets = set(secrets.keys()) - KNOWN_SECRETS_KEYS
        for key in sorted(unknown_secrets):
            click.echo(f"Warning: unknown secrets key '{key}'", err=True)

        setup: list[SetupStep] = []
        for i, entry in enumerate(raw.get("setup", []) or []):
            if not isinstance(entry, dict):
                raise click.ClickException(
                    f"setup[{i}] must be a mapping with `uses` or `run`"
                )
            if "raw" in entry:
                raise click.ClickException(
                    f"setup[{i}]: `raw` was removed. Split into multiple "
                    "setup entries, or move the YAML into a local "
                    "composite action and reference it with `uses`."
                )
            unknown = set(entry.keys()) - ALLOWED_STEP_FIELDS
            if unknown:
                raise click.ClickException(
                    f"setup[{i}]: unknown field(s): {', '.join(sorted(unknown))}. "
                    f"Allowed: {', '.join(sorted(ALLOWED_STEP_FIELDS))}."
                )
            step_keys = {"uses", "run"} & entry.keys()
            if len(step_keys) != 1:
                raise click.ClickException(
                    f"setup[{i}] must have exactly one of `uses` or `run`"
                )
            for k in DICT_STEP_FIELDS:
                if k in entry and not isinstance(entry[k], dict):
                    raise click.ClickException(f"setup[{i}]: `{k}` must be a mapping")
            setup.append(SetupStep(fields=dict(entry)))

        workflows: dict[str, WorkflowConfig] = {}
        for name, wf_raw in (raw.get("workflows") or {}).items():
            if name == "renovate":
                raise click.ClickException(
                    "workflows.renovate has been renamed to workflows.weekly"
                )
            if name not in KNOWN_WORKFLOWS:
                click.echo(
                    f"Warning: unknown workflow '{name}' in config (known: {', '.join(sorted(KNOWN_WORKFLOWS))})",
                    err=True,
                )
            if isinstance(wf_raw, dict):
                watched = wf_raw.get("watched_workflows")
                if watched is not None and len(watched) == 0 and name == "ci-fix":
                    raise click.ClickException(
                        "watched_workflows: [] is invalid for ci-fix — "
                        "workflow_run requires at least one workflow name. "
                        "Disable ci-fix with enabled: false instead."
                    )
                workflow_extra = wf_raw.get("workflow_extra")
                if workflow_extra is not None and not isinstance(workflow_extra, dict):
                    raise click.ClickException(
                        f"workflows.{name}.workflow_extra must be a mapping"
                    )
                jobs_raw = wf_raw.get("jobs")
                if jobs_raw is not None and (
                    not isinstance(jobs_raw, dict)
                    or not all(isinstance(v, dict) for v in jobs_raw.values())
                ):
                    raise click.ClickException(
                        f"workflows.{name}.jobs must be a mapping of mappings"
                    )
                workflows[name] = WorkflowConfig(
                    enabled=wf_raw.get("enabled", True),
                    prompt=wf_raw.get("prompt", ""),
                    cron=wf_raw.get("cron", ""),
                    watched_workflows=watched,
                    branches=wf_raw.get("branches"),
                    workflow_extra=workflow_extra,
                    jobs=jobs_raw,
                )
            else:
                workflows[name] = WorkflowConfig(enabled=bool(wf_raw))

        allowed = secrets.get("allowed", [])
        if not isinstance(allowed, list) or not all(
            isinstance(s, str) for s in allowed
        ):
            raise click.ClickException(
                "secrets.allowed must be a list of strings, "
                'e.g. allowed: ["CODECOV_TOKEN"]'
            )

        return cls(
            bot_name=bot_name,
            default_branch="main",
            protected_branches=protected_branches,
            bot_token_secret=secrets.get("bot_token", "BOT_TOKEN"),
            claude_token_secret=secrets.get("claude_token", "CLAUDE_CODE_OAUTH_TOKEN"),
            anthropic_api_key_secret=secrets.get(
                "anthropic_api_key", "ANTHROPIC_API_KEY"
            ),
            openai_key_secret=secrets.get("openai_key", "OPENAI_API_KEY"),
            codex_auth_json_secret=secrets.get("codex_auth_json", "CODEX_AUTH_JSON"),
            harness=harness,
            model=model,
            effort=effort,
            setup=setup,
            workflows=workflows,
            allowed_repo_secrets=allowed,
        )
