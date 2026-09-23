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


STANDARD_WORKFLOWS = {
    "review",
    "mention",
    "triage",
    "ci-fix",
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
    # Generated whenever at least one workflow uses Codex. It still honors
    # the common workflow enabled/override contract.
    "codex-auth-refresh",
    # install-test is opt-in via `tend init --with-install-test` but still
    # honors workflow_extra / jobs overrides from .config/tend.yaml.
    "install-test",
}
KNOWN_TOP_LEVEL = {
    "bot_name",
    "merge",
    "control_plane_owner",
    "memory_gist",
    "harness",
    "model",
    "effort",
    "args",
    "protected_branches",
    "secrets",
    "setup",
    # Deprecated; see `_migrated_sandbox_steps`.
    "sandbox_env",
    "sandbox_path",
    "sandbox_setup",
    "workflows",
}
KNOWN_HARNESSES = {"claude", "codex"}
KNOWN_MERGE_POLICIES = {"maintainer", "yolo"}
KNOWN_SECRETS_KEYS = {"allowed"}

# The operational secrets, by fixed name. Claude reads the OAuth token
# (subscription) or the API key (console.anthropic.com) — consumers set one;
# Codex reads either the OpenAI key or an access-only ChatGPT auth bundle.
# Not configurable: `install-tend` creates the
# `tend` environment and fills it from scratch, so there is no pre-existing
# secret whose name a consumer would want to keep.
BOT_TOKEN_SECRET = "TEND_BOT_TOKEN"
CLAUDE_TOKEN_SECRET = "CLAUDE_CODE_OAUTH_TOKEN"
ANTHROPIC_API_KEY_SECRET = "ANTHROPIC_API_KEY"
OPENAI_KEY_SECRET = "OPENAI_API_KEY"
CODEX_AUTH_SECRET = "CODEX_AUTH_JSON"
CODEX_REFRESH_AUTH_SECRET = "CODEX_REFRESH_AUTH_JSON"
CODEX_REFRESH_PAT_SECRET = "CODEX_REFRESH_PAT"
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
_CODEOWNER_USER = re.compile(r"^@[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?$")
# POSIX-ish env var name: letters, digits, underscore; not starting with a digit.
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

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
    "maintainer": MergePolicy(
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


def _deprecated_list(raw: dict, key: str) -> list[str]:
    values = raw.get(key) or []
    if not isinstance(values, list) or not all(
        isinstance(value, str) and value.strip() for value in values
    ):
        raise click.ClickException(f"{key} must be a list of non-empty strings")
    return values


def _deprecated_env(raw: dict) -> dict[str, str]:
    values = raw.get("sandbox_env") or {}
    if not isinstance(values, dict):
        raise click.ClickException(
            "sandbox_env must be a mapping of NAME: VALUE "
            '(e.g. sandbox_env: {RUST_BACKTRACE: "1"})'
        )
    env: dict[str, str] = {}
    for name, value in values.items():
        if not isinstance(name, str) or not _ENV_NAME.match(name):
            raise click.ClickException(
                f"sandbox_env key '{name}' is not a valid environment "
                "variable name (letters, digits, underscore; not starting "
                "with a digit)"
            )
        # `bool` is an `int` subclass, so it is tested first, and written as
        # the shell's `true`/`false` rather than Python's `True`/`False`.
        if isinstance(value, bool):
            env[name] = "true" if value else "false"
        elif isinstance(value, (str, int, float)):
            env[name] = str(value)
        else:
            raise click.ClickException(
                f"sandbox_env value for '{name}' must be a scalar "
                "(string, number, or boolean)"
            )
        # `$GITHUB_ENV` reads one NAME=VALUE per line, so a second line
        # would set a variable of its own.
        if "\n" in env[name]:
            raise click.ClickException(
                f"sandbox_env value for '{name}' must be a single line"
            )
    return env


def _migrated_sandbox_steps(raw: dict) -> list[SetupStep]:
    """`setup:` steps doing what the deprecated `sandbox_*` keys did.

    All three existed to reach an agent with its own environment, home and
    checkout. Under the copy-on-write view it runs with the job's environment
    and PATH and sees what `setup:` built, so their documented migration is to
    move the entries into `setup:`, and this performs it: one step per key,
    appended after the consumer's own steps, so those still do not see a
    `sandbox_env` value. Variables and paths come first, since `sandbox_setup`
    ran with both. A variable's value stays in the step's `env:`, where an
    Actions expression still evaluates, rather than in its script. A leading
    `~` named the sandbox's home, which under the view is the job's `$HOME`.
    The runner puts a later `$GITHUB_PATH` line ahead of an earlier one, so the
    directories are written last-first to keep the first entry first. The
    commands share one `-eo pipefail` bash, as they did, so a `cd`, `export` or
    `source` still reaches the ones after it.

    Warned about rather than refused, at the maintainer's call and against the
    no-backward-compatibility rule in CLAUDE.md, so that nothing breaks in a
    consumer before it migrates; a warning that dropped the entries would
    silently stop installing what its agent relies on.
    TODO(2026-10-21): refuse all three keys, with these messages as the
    migration, once the consumers that set them have moved their entries into
    `setup:`.
    """
    steps: list[SetupStep] = []
    env = _deprecated_env(raw)
    if env:
        click.echo(
            "Warning: `sandbox_env` is deprecated and will be refused in a "
            "later release. The agent now runs with the job's own "
            "environment, so a variable a `setup:` step exports to "
            "`$GITHUB_ENV` reaches it; its entries are exported from a "
            "`setup:` step after yours for now. Export them yourself from "
            'the last `setup:` step (e.g. `- run: echo "MY_TOKEN=$MY_TOKEN" '
            '>> "$GITHUB_ENV"`, with the value under the step\'s `env:` as '
            "`MY_TOKEN:`, where an expression still works), since every step "
            "after the export sees the value, and delete the key.",
            err=True,
        )
        run = "\n".join(f'echo "{name}=${name}" >> "$GITHUB_ENV"' for name in env)
        steps.append(SetupStep(fields={"run": run, "env": env}))
    paths = _deprecated_list(raw, "sandbox_path")
    if paths:
        click.echo(
            "Warning: `sandbox_path` is deprecated and will be refused in a "
            "later release. The agent now runs with the job's own PATH, so "
            "a directory a `setup:` step adds reaches it; its entries are "
            "added from a `setup:` step after yours for now. Add each "
            "directory yourself (e.g. "
            '`- run: echo "$HOME/.cargo/bin" >> "$GITHUB_PATH"`) and delete '
            "the key.",
            err=True,
        )
        directories = [
            "$HOME" + d[1:] if d == "~" or d.startswith("~/") else d for d in paths
        ]
        run = "\n".join(f'echo "{d}" >> "$GITHUB_PATH"' for d in reversed(directories))
        steps.append(SetupStep(fields={"run": run}))
    commands = _deprecated_list(raw, "sandbox_setup")
    if commands:
        click.echo(
            "Warning: `sandbox_setup` is deprecated and will be refused in a "
            "later release. The agent now works in the job's own checkout "
            "and home, so what `setup:` builds reaches it; its commands run "
            "in one `setup:` step after yours for now. That is an ordinary "
            "workflow step, and tend puts nothing on its PATH, so a command "
            "that calls `uv` needs a step that installs it, such as "
            "`astral-sh/setup-uv`, earlier in your `setup:`. Move the "
            "commands into `setup:` as `run:` steps (e.g. "
            "`- run: rustup component add clippy`) and delete the key; a "
            "`cd`, `export` or `source` reaches only the rest of its own "
            "step. `setup:` runs on the default or PR base tree; what a pull "
            "request itself changes, such as a new dependency in its "
            "lockfile, the agent installs in the session.",
            err=True,
        )
        steps.append(SetupStep(fields={"run": "\n".join(commands), "shell": "bash"}))
    return steps


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


# Models are not enumerated for either harness. Both catalogs churn, both
# accept an alias (`opus`) or an exact id (`claude-opus-5`, which a consumer
# pins to keep behavior fixed across a promotion), and a stale allowlist
# would refuse a newer model the CLI accepts. Any non-empty string passes
# through to `--model`; an unknown one fails the job with the CLI's own
# message naming it.
DEFAULT_MODEL_BY_HARNESS = {
    "claude": "opus",
    "codex": "gpt-5.6-sol",
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


# Empty string leaves effort at the harness CLI's model-specific default.
KNOWN_EFFORTS_BY_HARNESS = {
    "claude": {"", "low", "medium", "high", "xhigh", "max"},
    "codex": {"", "low", "medium", "high", "xhigh"},
}


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
    # Exact additional argv elements passed to the selected harness CLI.
    args: list[str] = field(default_factory=list)
    # Owner of the repo where workflows will run. Used to gate jobs that fail
    # noisily on forks (no access to bot/Claude secrets). Not user-configurable;
    # cli.init populates this via `gh repo view` so fork-based maintainer
    # workflows still get the canonical owner. Empty means "skip the guard"
    # (gh unavailable, or no default repo configured).
    repo_owner: str = ""
    allowed_repo_secrets: list[str] = field(default_factory=list)
    # Opt-in experiment that persists Claude Code's model-authored auto memory
    # in a bot-owned secret Gist. The Gist ID stays in a fixed environment
    # secret so a public repository does not publish the unlisted URL.
    memory_gist: bool = False
    merge: str = "maintainer"
    control_plane_owner: str = ""

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
        prefix = f"/tend-ci-runner:{skill}" if self.harness == "claude" else f"${skill}"
        return f"{prefix} {args}".rstrip()

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
        try:
            raw = _YAML.load(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, YAMLError) as error:
            raise click.ClickException(f"Could not parse {path}: {error}") from error
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
        if not isinstance(model, str) or not model.strip():
            raise click.ClickException(
                "model must be a non-empty string naming a model the "
                f"{harness} CLI accepts"
            )

        effort = _parse_effort(raw.get("effort", ""), harness, "effort")

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

        merge = raw.get("merge", "maintainer")
        if merge not in KNOWN_MERGE_POLICIES:
            raise click.ClickException(
                f"merge '{merge}' is not recognized "
                f"(known: {', '.join(sorted(KNOWN_MERGE_POLICIES))})"
            )
        control_plane_owner = raw.get("control_plane_owner", "")
        if not isinstance(control_plane_owner, str):
            raise click.ClickException("control_plane_owner must be a string")
        control_plane_owner = control_plane_owner.strip()
        if control_plane_owner and not _CODEOWNER_USER.fullmatch(control_plane_owner):
            raise click.ClickException(
                "control_plane_owner must be one GitHub user, such as '@octocat'; "
                "teams are not accepted because Tend cannot prove the bot is not "
                "a member"
            )
        if merge == "yolo" and not control_plane_owner:
            raise click.ClickException(
                "control_plane_owner is required when merge is 'yolo'"
            )
        if control_plane_owner.casefold() == f"@{bot_name}".casefold():
            raise click.ClickException(
                "control_plane_owner must not be the Tend bot account"
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
        setup.extend(_migrated_sandbox_steps(raw))

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
                branches = wf_raw.get("branches")
                # Both render straight into the `workflow_run:` trigger through
                # `tojson`, so an unchecked value reaches the workflow file
                # verbatim: `watched_workflows: ci` becomes `workflows: "ci"`,
                # which GitHub matches against nothing, and ci-fix silently
                # never fires. A number gets no further than `len()` below,
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
                        eff_harness,
                        f"workflows.{name}.effort",
                    )
                    if "effort" in wf_raw
                    else None
                )
                # A workflow that switches harness inherits the top-level
                # effort, which the other CLI may not accept.
                if wf_effort is None and wf_harness is not None:
                    _parse_effort(
                        effort,
                        eff_harness,
                        "effort",
                        inherited_by=f"workflows.{name}",
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
                workflows[name] = WorkflowConfig(
                    enabled=wf_raw.get("enabled", True),
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
                )
            else:
                workflows[name] = WorkflowConfig(enabled=bool(wf_raw))

        # Both harnesses run behind the same credential-isolation sandbox;
        # these levers therefore apply to either one.
        enabled_harnesses = _enabled_harnesses(harness, workflows)
        if memory_gist and "claude" not in enabled_harnesses:
            raise click.ClickException(
                "memory_gist is experimental and requires at least one enabled "
                "workflow using the Claude harness"
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
            control_plane_owner=control_plane_owner,
            workflows=workflows,
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


def _parse_effort(
    raw: object,
    harness: str,
    key: str,
    *,
    inherited_by: str | None = None,
) -> str:
    """Validate an effort value against the CLI selected for it, not the model:
    which models read the level is the harness CLI's to know."""
    source = key if inherited_by is None else f"{key} (inherited by {inherited_by})"
    known = KNOWN_EFFORTS_BY_HARNESS[harness]
    if not isinstance(raw, str) or raw not in known:
        raise click.ClickException(
            f"{source} '{raw}' is not recognized for harness '{harness}' "
            f"(known: {', '.join(sorted(e for e in known if e))})"
        )
    return raw


def _enabled_harnesses(harness: str, workflows: dict[str, WorkflowConfig]) -> set[str]:
    """Effective harnesses of enabled, normally generated workflows."""
    enabled = set()
    for name in STANDARD_WORKFLOWS:
        workflow = workflows.get(name, WorkflowConfig())
        if not workflow.enabled:
            continue
        if name == "ci-fix" and workflow.watched_workflows is None:
            continue
        enabled.add(workflow.harness or harness)
    return enabled
