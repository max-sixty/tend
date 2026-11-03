"""Read and validate .config/tend.yaml."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import click
from ruamel.yaml import YAML, YAMLError

# ruamel.yaml parses YAML 1.2 by default, which fixes PyYAML's `on:` → True
# trap and the Norway problem (yes/no/on/off coerced to bool).
_YAML = YAML(typ="safe", pure=True)


SKILL_PREFIX = {"claude": "/tend-ci-runner:", "codex": "$"}

STANDARD_WORKFLOWS = {
    "review",
    "mention",
    "triage",
    "nightly",
    "weekly",
    "notifications",
    "review-runs",
}
KNOWN_WORKFLOWS = {
    *STANDARD_WORKFLOWS,
    # Generated with mention to carry its review events; runs no agent. Honors
    # the common workflow enabled/override contract.
    "mention-relay",
    # Files an issue for each watched workflow failing on a watched branch,
    # which triage then works; runs no agent. Generated once
    # `watched_workflows` names the workflows to watch.
    "ci-failures",
    # Generated for Codex with hosted auth refresh. This entry configures its
    # schedule and job overrides; codex.auth_refresh owns whether it exists.
    "codex-auth-refresh",
    # install-test is opt-in via `tend init --with-install-test` but still
    # honors workflow_extra / jobs overrides from .config/tend.yaml.
    "install-test",
}
KNOWN_TOP_LEVEL = {
    "bot_name",
    "merge",
    "memory_gist",
    "harness",
    "codex",
    "model",
    "effort",
    "args",
    "protected_branches",
    "secrets",
    "setup",
    "workflows",
}
KNOWN_HARNESSES = {"claude", "codex"}
KNOWN_MERGE_POLICIES = {"restricted", "yolo"}
KNOWN_SECRETS_KEYS = {"allowed"}

# The operational secrets, by fixed name. Claude reads the OAuth token
# (subscription) or the API key (console.anthropic.com) — consumers set one;
# Codex reads either the OpenAI key or an access-only ChatGPT auth bundle.
# Not configurable: `install-tend` creates the `tend` and subscription refresh
# environments and fills them from scratch, so there is no pre-existing secret
# whose name a consumer would want to keep.
BOT_TOKEN_SECRET = "TEND_BOT_TOKEN"
CLAUDE_TOKEN_SECRET = "CLAUDE_CODE_OAUTH_TOKEN"
ANTHROPIC_API_KEY_SECRET = "ANTHROPIC_API_KEY"
OPENAI_KEY_SECRET = "OPENAI_API_KEY"
CODEX_AUTH_SECRET = "CODEX_AUTH_JSON"
CODEX_REFRESH_AUTH_SECRET = "CODEX_REFRESH_AUTH_JSON"
CODEX_REFRESH_PAT_SECRET = "CODEX_REFRESH_PAT"
CODEX_REFRESH_ENVIRONMENT = "tend-codex-refresh"
MEMORY_GIST_SECRET = "TEND_MEMORY_GIST_ID"
OPERATIONAL_SECRETS = {
    MEMORY_GIST_SECRET,
    BOT_TOKEN_SECRET,
    CLAUDE_TOKEN_SECRET,
    ANTHROPIC_API_KEY_SECRET,
    OPENAI_KEY_SECRET,
    CODEX_AUTH_SECRET,
    CODEX_REFRESH_AUTH_SECRET,
    CODEX_REFRESH_PAT_SECRET,
}
# Keys that once renamed those secrets. A leftover one is refused rather
# than warned past: ignoring it would generate workflows reading the fixed
# name while the consumer's secret still answers to the old one, and every
# job would fail on an empty token.
REMOVED_SECRETS_KEYS = {
    "bot_token": BOT_TOKEN_SECRET,
    "claude_token": CLAUDE_TOKEN_SECRET,
    "anthropic_api_key": ANTHROPIC_API_KEY_SECRET,
    "openai_key": OPENAI_KEY_SECRET,
}
_GITHUB_USERNAME = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?$")
# POSIX-ish env var name: letters, digits, underscore; not starting with a digit.

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
    """A single trusted runner-side setup step.

    Exactly one of `uses` or `run`, plus any of `with`, `env`, `name`, `id`,
    `shell`, `working-directory`, `continue-on-error`, `timeout-minutes`, `if`.
    In workflows that pre-check whether the agent needs to boot, the renderer
    adds that check to every step's `if:`. A step's own condition narrows the
    check. For multi-step setup, add multiple entries to the `setup:` list or
    reference a local composite action with `uses`.
    """

    fields: dict


@dataclass(frozen=True)
class MergePolicy:
    """The small set of decisions that changes between merge modes."""

    bot_can_merge: bool
    expected_runtime_bypass: str
    requires_control_plane_review: bool


MERGE_POLICIES = {
    "restricted": MergePolicy(
        bot_can_merge=False,
        expected_runtime_bypass="never",
        requires_control_plane_review=False,
    ),
    "yolo": MergePolicy(
        bot_can_merge=True,
        expected_runtime_bypass="pull_requests_only",
        requires_control_plane_review=True,
    ),
}


@dataclass
class WorkflowConfig:
    enabled: bool = True
    prompt: str = ""
    cron: str = ""
    watched_workflows: list[str] | None = None
    branches: list[str] | None = None
    workflow_extra: dict | None = None
    jobs: dict[str, dict] | None = None
    # Per-workflow harness override. Lets consumers trial a new harness on
    # a single workflow (e.g. `codex` on nightly only) before flipping the
    # whole bot. None means inherit from top-level `harness`.
    harness: str | None = None
    # Per-workflow model override. None inherits the top-level model when the
    # harness is unchanged, or uses the target harness's default when it changes.
    model: str | None = None
    # Per-workflow effort and CLI argument overrides. None means inherit from
    # the top level; an empty args list clears top-level arguments.
    effort: str | None = None
    args: list[str] | None = None
    # Review only: whether the review waits for the reviewed commit's CI.
    await_ci: bool = True
    # Triage only: simultaneous issue jobs within the serialized batch.
    max_parallel: int = 5


# Models are not enumerated for either harness. Both catalogs churn, both
# accept an alias (`opus`) or an exact id (`claude-opus-5`, which a consumer
# pins to keep behavior fixed across a promotion), and a stale allowlist
# would refuse a newer model the CLI accepts. Any non-empty string passes
# through to `--model`; an unknown one fails the job with the CLI's own
# message naming it.
DEFAULT_MODEL_BY_HARNESS = {
    "claude": "opus",
    "codex": "gpt-6.1-sol",
}


def effective_model(
    harness: str,
    model: str,
    workflow_harness: str | None,
    workflow_model: str | None,
) -> str:
    """Resolve a workflow's model, resetting defaults when its harness changes."""
    if workflow_model is not None:
        return workflow_model
    if workflow_harness is not None and workflow_harness != harness:
        return DEFAULT_MODEL_BY_HARNESS[workflow_harness]
    return model


@dataclass(frozen=True)
class CodexConfig:
    """Codex subscription credentials have one hosted or external refresh owner."""

    auth_refresh: bool = True


@dataclass
class Config:
    bot_name: str
    default_branch: str
    protected_branches: list[str]
    harness: str
    model: str
    effort: str
    setup: list[SetupStep]
    workflows: dict[str, WorkflowConfig]
    codex: CodexConfig = field(default_factory=CodexConfig)
    # Exact additional argv elements passed to the selected harness CLI.
    args: list[str] = field(default_factory=list)
    # Owner of the repo where workflows will run. Used to gate jobs that fail
    # noisily on forks (no access to bot/Claude secrets). Not user-configurable;
    # cli.init populates this via `gh repo view` so workflows in forks still
    # get the canonical owner. Empty means "skip the guard"
    # (gh unavailable, or no default repo configured).
    repo_owner: str = ""
    allowed_repo_secrets: list[str] = field(default_factory=list)
    # Opt-in experiment that persists agent-authored Markdown memory
    # in a bot-owned secret Gist. The Gist ID stays in a fixed environment
    # secret so a public repository does not publish the unlisted URL.
    memory_gist: bool = False
    merge: str = "restricted"

    @property
    def merge_policy(self) -> MergePolicy:
        return MERGE_POLICIES[self.merge]

    def enabled_harnesses(self) -> set[str]:
        """Harnesses used by the workflows a normal regeneration emits."""
        return _enabled_harnesses(self.harness, self.workflows)

    def default_prompt(self, skill: str, args: str = "") -> str:
        """Default prompt invoking a tend-ci-runner skill in harness-native syntax.

        Claude resolves `/tend-ci-runner:NAME` as a slash command. Codex resolves
        `$NAME` as a skill mention (or matches by description); the
        `tend-ci-runner` namespace prefix isn't needed at the prompt site because
        skill names within the plugin are unique. `args` is appended raw so
        callers can splice their own placeholders (`{pr_number}` etc.) and run
        the existing replace step.
        """
        prefix = SKILL_PREFIX[self.harness] + skill
        return f"{prefix} {args}".rstrip()

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        if path is None:
            path = Path(".config/tend.yaml")
        if path.suffix == ".toml":
            raise click.ClickException(
                f"Unsupported config format: {path}. Convert it to YAML."
            )
        if not path.exists():
            legacy = Path(".config/tend.toml")
            if path == Path(".config/tend.yaml") and legacy.exists():
                raise click.ClickException(
                    f"Unsupported config format: {legacy}. "
                    f"Convert it to YAML at {path}."
                )
            raise click.ClickException(f"Config not found: {path}")
        try:
            raw = _YAML.load(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, YAMLError) as error:
            raise click.ClickException(f"Could not parse {path}: {error}") from error
        if not isinstance(raw, dict):
            raise click.ClickException(
                f"{path} must contain a YAML mapping at the top level"
            )

        retired = sorted(set(raw) & {"sandbox_env", "sandbox_path", "sandbox_setup"})
        if retired:
            raise click.ClickException(
                f"Unsupported config keys: {', '.join(retired)}. "
                "Move commands into setup: run: steps; export environment variables "
                "to $GITHUB_ENV and add PATH entries to $GITHUB_PATH in those steps."
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
        if not isinstance(model, str) or not model.strip():
            raise click.ClickException(
                "model must be a non-empty string naming a model the "
                f"{harness} CLI accepts"
            )

        effort = _parse_effort(raw.get("effort", ""), "effort")

        args = _parse_args(raw.get("args", []), "args")

        memory_gist = raw.get("memory_gist", False)
        if not isinstance(memory_gist, bool):
            raise click.ClickException("memory_gist must be true or false")

        # Refused rather than warned past as unknown: a config that paused tend
        # would otherwise regenerate running workflows.
        if "enabled" in raw:
            raise click.ClickException(
                "Top-level `enabled` was removed; pausing is now the "
                "TEND_ENABLED repository variable. To keep tend paused, run "
                "`gh variable set TEND_ENABLED --body false` before removing "
                "the key."
            )

        merge = raw.get("merge", "restricted")
        if merge not in KNOWN_MERGE_POLICIES:
            raise click.ClickException(
                f"merge '{merge}' is not recognized "
                f"(known: {', '.join(sorted(KNOWN_MERGE_POLICIES))})"
            )
        if "control_plane_owner" in raw:
            raise click.ClickException(
                "control_plane_owner was removed; remove it and run `tend check --fix` to "
                "create the control-plane CODEOWNERS block"
            )

        unknown = set(raw.keys()) - KNOWN_TOP_LEVEL
        for key in sorted(unknown):
            click.echo(f"Warning: unknown config key '{key}'", err=True)

        codex_raw = raw.get("codex", {})
        if not isinstance(codex_raw, dict):
            raise click.ClickException("codex must be a mapping")
        unknown_codex = set(codex_raw) - {"auth_refresh"}
        if unknown_codex:
            raise click.ClickException(
                f"Unknown codex option(s): {', '.join(sorted(unknown_codex))}"
            )
        auth_refresh = codex_raw.get("auth_refresh", True)
        if not isinstance(auth_refresh, bool):
            raise click.ClickException("codex.auth_refresh must be true or false")
        codex = CodexConfig(auth_refresh=auth_refresh)

        protected_branches = raw.get("protected_branches", [])
        if not isinstance(protected_branches, list) or not all(
            isinstance(b, str) and b for b in protected_branches
        ):
            raise click.ClickException(
                "protected_branches must be a list of non-empty strings"
            )

        secrets = raw.get("secrets", {}) or {}
        removed = sorted(set(secrets) & set(REMOVED_SECRETS_KEYS))
        if removed:
            renames = ", ".join(
                f"secrets.{key} → {REMOVED_SECRETS_KEYS[key]}" for key in removed
            )
            raise click.ClickException(
                f"Removed secret name override(s): {renames}. The operational "
                "secret names are fixed — rename each secret to the name shown "
                "and drop the key."
            )
        unknown_secrets = set(secrets) - KNOWN_SECRETS_KEYS
        for key in sorted(unknown_secrets):
            click.echo(f"Warning: unknown secrets key '{key}'", err=True)

        setup_raw = raw.get("setup", []) or []
        setup: list[SetupStep] = []
        for i, entry in enumerate(setup_raw):
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
            if "with" in entry and "run" in entry:
                raise click.ClickException(
                    f"setup[{i}]: `with` is only valid for action steps"
                )
            for k in DICT_STEP_FIELDS:
                if k in entry and not isinstance(entry[k], dict):
                    raise click.ClickException(f"setup[{i}]: `{k}` must be a mapping")
            if "if" in entry:
                condition = entry["if"]
                if not isinstance(condition, str) or not condition.strip():
                    raise click.ClickException(
                        f"setup[{i}]: `if` must be a non-empty string; quote "
                        'it (`if: "false"`) so YAML does not read it as a '
                        "boolean, number, or list"
                    )
                condition = condition.strip()
                if condition.startswith("${{") and condition.endswith("}}"):
                    condition = condition[3:-2].strip()
                    if not condition:
                        raise click.ClickException(
                            f"setup[{i}]: `if` must contain an expression"
                        )
                if "${{" in condition or "}}" in condition:
                    raise click.ClickException(
                        f"setup[{i}]: `if` must be a plain expression or one "
                        "whole `${{ ... }}` expression"
                    )
                entry = {**entry, "if": condition}
            setup.append(SetupStep(fields=dict(entry)))

        workflows: dict[str, WorkflowConfig] = {}
        for name, wf_raw in (raw.get("workflows") or {}).items():
            if name == "codex-auth-refresh" and (
                isinstance(wf_raw, bool)
                or isinstance(wf_raw, dict)
                and "enabled" in wf_raw
            ):
                raise click.ClickException(
                    "workflows.codex-auth-refresh.enabled was removed; "
                    "use codex.auth_refresh: true or false instead"
                )
            if name == "renovate":
                raise click.ClickException(
                    "workflows.renovate has been renamed to workflows.weekly"
                )
            if name == "ci-fix":
                raise click.ClickException(
                    "workflows.ci-fix was replaced by workflows.ci-failures, which "
                    "files an issue when a watched workflow fails and leaves the "
                    "fix to triage. Rename the key; `watched_workflows`, "
                    "`branches` and `enabled` keep their meaning. It runs no "
                    "agent, so drop any `prompt`, `harness`, `model`, `effort` or "
                    "`args` there, and put CI-specific instructions in the "
                    "repo's `running-tend` skill overlay instead."
                )
            if name not in KNOWN_WORKFLOWS:
                click.echo(
                    f"Warning: unknown workflow '{name}' in config (known: {', '.join(sorted(KNOWN_WORKFLOWS))})",
                    err=True,
                )
            # YAML 1.2 reads `no`/`off` as strings, which are truthy, and a bare
            # `review:` is null, which was falsy; unchecked, either one
            # generates or omits a workflow against what the consumer wrote.
            if isinstance(wf_raw, dict):
                enabled = wf_raw.get("enabled", True)
                if not isinstance(enabled, bool):
                    raise click.ClickException(
                        f"workflows.{name}.enabled must be true or false"
                    )
            elif isinstance(wf_raw, bool):
                enabled = wf_raw
            else:
                raise click.ClickException(
                    f"workflows.{name} must be true, false, or a mapping"
                )
            if isinstance(wf_raw, dict):
                watched = wf_raw.get("watched_workflows")
                branches = wf_raw.get("branches")
                # Both render straight into the `workflow_run:` trigger through
                # `tojson`, so an unchecked value reaches the workflow file
                # verbatim: `watched_workflows: ci` becomes `workflows: "ci"`,
                # which GitHub matches against nothing, and ci-failures
                # silently never fires. A number gets no further than `len()` below,
                # which raises a bare TypeError instead of a config error.
                for key, value, example in (
                    ("watched_workflows", watched, '["ci"]'),
                    ("branches", branches, '["main"]'),
                ):
                    if value is not None and (
                        not isinstance(value, list)
                        or not all(isinstance(s, str) and s for s in value)
                    ):
                        raise click.ClickException(
                            f"workflows.{name}.{key} must be a list of "
                            f"non-empty strings (e.g. {key}: {example})"
                        )
                if branches is not None and not branches:
                    raise click.ClickException(
                        f"workflows.{name}.branches: [] matches no branch — "
                        "omit the key to default to the repository's default "
                        "branch, or list the branches to watch."
                    )
                if watched is not None and len(watched) == 0 and name == "ci-failures":
                    raise click.ClickException(
                        "watched_workflows: [] is invalid for ci-failures — "
                        "workflow_run requires at least one workflow name. "
                        "Disable ci-failures with enabled: false instead."
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
                if merge == "yolo" and (
                    workflow_extra is not None or jobs_raw is not None
                ):
                    raise click.ClickException(
                        f"workflows.{name}: workflow_extra and jobs overrides are "
                        "not allowed when merge is 'yolo'; generated runner jobs "
                        "must retain their audited shape"
                    )
                wf_harness = wf_raw.get("harness")
                wf_model = wf_raw.get("model")
                wf_args = (
                    _parse_args(wf_raw["args"], f"workflows.{name}.args")
                    if "args" in wf_raw
                    else None
                )
                if wf_harness is not None and wf_harness not in KNOWN_HARNESSES:
                    raise click.ClickException(
                        f"workflows.{name}.harness '{wf_harness}' is not recognized "
                        f"(known: {', '.join(sorted(KNOWN_HARNESSES))})"
                    )
                eff_harness = wf_harness or harness
                if wf_model is not None and (
                    not isinstance(wf_model, str) or not wf_model.strip()
                ):
                    raise click.ClickException(
                        f"workflows.{name}.model must be a non-empty string "
                        f"naming a model the {eff_harness} CLI accepts"
                    )
                wf_effort = (
                    _parse_effort(
                        wf_raw["effort"],
                        f"workflows.{name}.effort",
                    )
                    if "effort" in wf_raw
                    else None
                )
                wf_prompt = wf_raw.get("prompt", "")
                if wf_prompt is None:  # `prompt:` with nothing after it
                    wf_prompt = ""
                if not isinstance(wf_prompt, str):
                    raise click.ClickException(
                        f"workflows.{name}.prompt must be a string, "
                        f"got {type(wf_prompt).__name__}"
                    )
                # `mention` builds its prompt from the triggering event —
                # which of five comment shapes fired, the queue delay, the
                # ids to read back — so there is no text an override could
                # replace without breaking the dispatch. Refuse it rather
                # than accept a key that renders nowhere.
                if wf_prompt and name == "mention":
                    raise click.ClickException(
                        "workflows.mention.prompt is not supported: mention "
                        "composes its prompt from the triggering event. Put "
                        "standing instructions in the repo's `running-tend` skill "
                        "overlay instead."
                    )
                # A whitespace-only prompt is truthy, so it beats the default
                # and leaves the agent step with no instructions — which the
                # Claude action fails on by name and the Codex action hands to
                # `codex exec` and runs. `""` and a bare `prompt:` are falsy and
                # fall through to the default instead, which is quieter but no
                # more what the consumer wrote. All three are typos; refuse them
                # here, where the key's presence still tells them apart from an
                # absent one.
                if "prompt" in wf_raw and not wf_prompt.strip():
                    raise click.ClickException(
                        f"workflows.{name}.prompt is blank. Drop the key to "
                        f"use the default prompt."
                    )
                max_parallel = wf_raw.get("max_parallel", WorkflowConfig.max_parallel)
                if "max_parallel" in wf_raw and name != "triage":
                    raise click.ClickException(
                        f"workflows.{name}.max_parallel is only supported for triage"
                    )
                if type(max_parallel) is not int or not 1 <= max_parallel <= 256:
                    raise click.ClickException(
                        f"workflows.{name}.max_parallel must be an integer from 1 to 256"
                    )
                await_ci = wf_raw.get("await_ci", True)
                if "await_ci" in wf_raw and name != "review":
                    raise click.ClickException(
                        f"workflows.{name}.await_ci is not supported: only "
                        "the review workflow reads it."
                    )
                if not isinstance(await_ci, bool):
                    raise click.ClickException(
                        f"workflows.{name}.await_ci must be true or false"
                    )
                workflows[name] = WorkflowConfig(
                    enabled=enabled,
                    prompt=wf_prompt,
                    cron=wf_raw.get("cron", ""),
                    watched_workflows=watched,
                    branches=branches,
                    workflow_extra=workflow_extra,
                    jobs=jobs_raw,
                    harness=wf_harness,
                    model=wf_model,
                    effort=wf_effort,
                    args=wf_args,
                    await_ci=await_ci,
                    max_parallel=max_parallel,
                )
            else:
                workflows[name] = WorkflowConfig(enabled=enabled)

        # Both harnesses run behind the same credential-isolation sandbox;
        # these levers therefore apply to either one.
        enabled_harnesses = _enabled_harnesses(harness, workflows)
        if memory_gist and not enabled_harnesses:
            raise click.ClickException(
                "memory_gist is experimental and requires at least one enabled "
                "agent workflow"
            )

        allowed = secrets.get("allowed", [])
        if not isinstance(allowed, list) or not all(
            isinstance(s, str) for s in allowed
        ):
            raise click.ClickException(
                "secrets.allowed must be a list of strings, "
                'e.g. allowed: ["CODECOV_TOKEN"]'
            )

        # The allowlist is the one deliberate exception to "no repo-level
        # secrets", for tokens whose exposure the maintainer accepts. The
        # operational secrets are never that: allowlisting one would let a
        # repo-level copy pass `tend check`, handing any workflow the bot
        # pushes exactly what the environment gate denies.
        blessed = OPERATIONAL_SECRETS & set(allowed)
        if blessed:
            raise click.ClickException(
                f"secrets.allowed must not include {', '.join(sorted(blessed))}: "
                "the operational secrets live in the 'tend' environment, and "
                "allowlisting a repo-level copy would let any workflow the bot "
                "pushes read it."
            )

        return cls(
            bot_name=bot_name,
            default_branch="main",
            protected_branches=protected_branches,
            harness=harness,
            model=model,
            effort=effort,
            args=args,
            setup=setup,
            memory_gist=memory_gist,
            merge=merge,
            workflows=workflows,
            codex=codex,
            allowed_repo_secrets=allowed,
        )


def _parse_args(raw: object, key: str) -> list[str]:
    """Validate exact CLI argument elements from one config key."""
    if not isinstance(raw, list) or not all(
        isinstance(arg, str)
        and arg.strip()
        and "\n" not in arg
        and "\r" not in arg
        and arg == arg.rstrip()
        for arg in raw
    ):
        raise click.ClickException(
            f"{key} must be a list of non-blank, single-line strings "
            "without trailing whitespace "
            '(e.g. ["--max-turns", "50"])'
        )
    return list(raw)


def _parse_effort(raw: object, key: str) -> str:
    """Pass effort names to the CLI that owns them; empty uses its default."""
    if not isinstance(raw, str):
        raise click.ClickException(
            f"{key} must be a string (empty leaves the harness CLI's default)"
        )
    return raw


def _enabled_harnesses(harness: str, workflows: dict[str, WorkflowConfig]) -> set[str]:
    """Effective harnesses of enabled, normally generated workflows."""
    enabled = set()
    for name in STANDARD_WORKFLOWS:
        workflow = workflows.get(name, WorkflowConfig())
        if not workflow.enabled:
            continue
        enabled.add(workflow.harness or harness)
    return enabled
