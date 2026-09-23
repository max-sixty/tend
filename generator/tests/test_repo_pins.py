"""Repo-wide invariants that no single suite owns.

Mostly pins: a version named in two places drifts silently unless something
asserts the pair (`test_pinned_mitmproxy_matches_the_action` in proxy/ is the
sibling of that idea). The rest are lints over a whole tree — a shape that
holds for every file, not a phrase pinned in one.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from tend.config import KNOWN_HARNESSES, Config
from tend.workflows import UV_VERSION

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_generated_workflow_snapshot_hook_covers_every_snapshot() -> None:
    config = YAML(typ="safe", pure=True).load(
        (REPO_ROOT / ".pre-commit-config.yaml").read_text()
    )
    hooks = [
        hook
        for repo in config["repos"]
        for hook in repo["hooks"]
        if hook.get("name") == "Lint generated workflow snapshots"
    ]
    assert len(hooks) == 1, f"expected one snapshot actionlint hook, got: {hooks}"

    snapshots = sorted((REPO_ROOT / "generator/tests/_regtest_outputs").glob("*.out"))
    assert snapshots, "no generated workflow snapshots found"

    pattern = re.compile(hooks[0]["files"])
    unmatched = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in snapshots
        if not pattern.search(path.relative_to(REPO_ROOT).as_posix())
    ]
    assert not unmatched, f"snapshot actionlint hook skips: {unmatched}"


def test_claude_transcript_summary_is_opt_in() -> None:
    action = YAML(typ="safe", pure=True).load(
        (REPO_ROOT / "claude" / "action.yaml").read_text()
    )

    assert action["inputs"]["show_full_output"]["default"] == "false"


def test_codex_action_does_not_choose_a_model_for_direct_callers() -> None:
    action = YAML(typ="safe", pure=True).load(
        (REPO_ROOT / "codex" / "action.yaml").read_text()
    )

    assert "default" not in action["inputs"]["model"]


def test_codex_agent_never_receives_the_pat_or_api_key() -> None:
    """Long-lived credentials stop in runner-owned auth and proxy steps."""
    action = YAML(typ="safe", pure=True).load(
        (REPO_ROOT / "codex" / "action.yaml").read_text()
    )
    steps = {step["name"]: step for step in action["runs"]["steps"]}

    assert "experimental" in action["name"].lower()
    setup_env = steps["Set up credential-isolation sandbox"]["env"]
    setup_run = steps["Set up credential-isolation sandbox"]["run"]
    assert setup_env["TEND_GH_TOKEN"] == "${{ inputs.github_token }}"
    assert setup_env["TEND_GITHUB_ONLY"] == "1"
    assert "TEND_OPENAI_API_KEY" not in setup_env
    assert 'TEND_GITHUB_ONLY="$TEND_GITHUB_ONLY"' in setup_run
    auth = steps["Configure Codex auth"]
    assert auth["env"]["OPENAI_API_KEY"] == "${{ inputs.openai_api_key }}"
    assert auth["env"]["CODEX_AUTH_JSON"] == "${{ inputs.codex_auth_json }}"
    assert "/usr/bin/env -i" in auth["run"]
    assert "tend-codex-auth.json" in auth["run"]
    openai_proxy = steps["Start OpenAI Responses proxy"]
    assert openai_proxy["if"] == "steps.codex_auth.outputs.mode == 'api-key'"
    assert openai_proxy["env"]["PROXY_API_KEY"] == "${{ inputs.openai_api_key }}"
    assert "exec /usr/bin/env -i" in openai_proxy["run"]
    assert '"$NODE_BIN" "$CODEX_PROXY_BIN"' in openai_proxy["run"]
    assert '<<< "$PROXY_API_KEY"' in openai_proxy["run"]
    assert '> "$PROXY_LOG_FILE" 2>&1 &' in openai_proxy["run"]
    assert 'cat "$PROXY_LOG_FILE" >&2' in openai_proxy["run"]
    assert "OPENAI_API_KEY is unset" in openai_proxy["run"]
    assert [
        name
        for name, step in steps.items()
        if "${{ inputs.openai_api_key }}" in str(step)
    ] == ["Configure Codex auth", "Start OpenAI Responses proxy"]
    assert [
        name
        for name, step in steps.items()
        if "${{ inputs.codex_auth_json }}" in str(step)
    ] == ["Configure Codex auth"]
    subscription = steps["Stage subscription auth (sandbox)"]
    assert subscription["if"] == "steps.codex_auth.outputs.mode == 'subscription'"
    assert subscription["env"] == {
        "BASH_ENV": "",
        "BASHOPTS": "",
        "SHELLOPTS": "",
        "PS4": "",
    }
    assert '"$AGENT_HOME/.codex/auth.json"' in subscription["run"]
    assert 'rm -f -- "$RUNNER_TEMP/tend-codex-auth.json"' in subscription["run"]
    run_env = steps["Run Codex"]["env"]
    assert not (
        {"OPENAI_API_KEY", "CODEX_AUTH_JSON", "GH_TOKEN", "GITHUB_TOKEN"}
        & run_env.keys()
    )
    assert steps["Run Codex"]["run"].endswith('launch_agent.py"')
    assert "CODEX_SANDBOX_MODE" not in run_env
    assert run_env["AUTH_MODE"] == "${{ steps.codex_auth.outputs.mode }}"
    assert steps["Token usage"]["env"]["SANDBOX_REAPED"] == (
        "${{ steps.codex.outputs.sandbox_reaped }}"
    )


def test_codex_action_drives_its_stateful_phases_through_the_runner() -> None:
    """The action is the only thing that reaches `runner.py`, three ways.

    Two steps invoke it directly, and `Run Codex` names it in `TEND_CODEX_RUNNER`
    for the third command, which `agent_lifecycle` issues from inside the
    sandbox. `runner.py` dispatches on an exact argv and indexes that variable,
    and nothing runs a composite action in CI, so a rename or a dropped
    variable first fails in a consumer's job. What the commands then do is
    covered by test_codex_runner.py and test_agent_lifecycle.py.
    """
    action = YAML(typ="safe", pure=True).load(
        (REPO_ROOT / "codex" / "action.yaml").read_text()
    )
    steps = action["runs"]["steps"]
    invoked = {
        step["run"].rsplit('"', 1)[-1].strip()
        for step in steps
        if "runner.py" in step.get("run", "")
    }

    assert {"install-plugin", "stage-agents"} <= invoked

    # launch_agent.py gates the passthrough on this being set, and
    # agent_lifecycle.py then indexes it — unset, the codex turn raises KeyError.
    run_codex = next(step for step in steps if step["name"] == "Run Codex")
    assert run_codex["env"]["TEND_CODEX_RUNNER"].endswith("/runner.py")


def test_codex_marketplace_declares_the_plugins_the_runner_installs() -> None:
    """`runner.py` copies this manifest into the sandbox and installs both
    plugins by name (`plugin add <name>@tend`, pinned in test_codex_runner.py).
    A rename here surfaces only as a failed Codex run.
    """
    marketplace = json.loads(
        (REPO_ROOT / ".agents" / "plugins" / "marketplace.json").read_text()
    )
    sources = {plugin["name"]: plugin["source"] for plugin in marketplace["plugins"]}

    assert {"install-tend", "tend-ci-runner"} <= set(sources)
    missing = sorted(
        name
        for name, source in sources.items()
        if not (REPO_ROOT / source["path"]).is_dir()
    )
    assert not missing, f"marketplace points at no directory for: {missing}"


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_sandbox_resources_are_removed_immediately_after_agent_reap(
    harness: str,
) -> None:
    action = YAML(typ="safe", pure=True).load(
        (REPO_ROOT / harness / "action.yaml").read_text()
    )
    steps = action["runs"]["steps"]
    run_name = "Run Claude" if harness == "claude" else "Run Codex"
    run_at = next(index for index, step in enumerate(steps) if step["name"] == run_name)
    cleanup_at = run_at + (2 if harness == "codex" else 1)
    if harness == "codex":
        stop = steps[run_at + 1]
        assert stop["name"] == "Stop OpenAI Responses proxy"
        assert stop["if"] == "always()"
        assert "/usr/bin/curl" in stop["run"]
        assert "--max-time 10" in stop["run"]
    cleanup = steps[cleanup_at]

    assert cleanup["name"] == "Dispose sandbox resources"
    assert cleanup["if"] == "always()"
    assert cleanup["run"].endswith('/dispose_sandbox_resources.py"')


def test_hosted_probe_launches_only_from_the_action_copy() -> None:
    script = (REPO_ROOT / "proxy" / "test-setup-sandbox.sh").read_text()
    invocation = '"$TEND_TEST_ACTION_PATH/shared/steps/launch_agent.py" || rc=$?'

    assert script.count(invocation) == 2
    assert "-s shared/steps/launch_agent.py" not in script


def test_npm_installs_use_distinct_empty_config_files() -> None:
    action = YAML(typ="safe", pure=True).load(
        (REPO_ROOT / "codex" / "action.yaml").read_text()
    )
    codex_install = next(
        step["run"]
        for step in action["runs"]["steps"]
        if step.get("name") == "Install Codex and Responses proxy"
    )
    assert 'mktemp "$TEND_PRIVATE_DIR/tend-npm-user.XXXXXX"' in codex_install
    assert 'mktemp "$TEND_PRIVATE_DIR/tend-npm-global.XXXXXX"' in codex_install
    assert '--userconfig "$npm_userconfig"' in codex_install
    assert '--globalconfig "$npm_globalconfig"' in codex_install


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_hardened_shells_scrub_bash_env(harness: str) -> None:
    action = YAML(typ="safe", pure=True).load(
        (REPO_ROOT / harness / "action.yaml").read_text()
    )

    for step in action["runs"]["steps"]:
        if "--noprofile" in step.get("shell", ""):
            assert step.get("env", {}).get("BASH_ENV") == "", step["name"]


def test_codex_hardened_shells_pin_command_resolution() -> None:
    action = YAML(typ="safe", pure=True).load(
        (REPO_ROOT / "codex" / "action.yaml").read_text()
    )
    safe_path = "PATH=/usr/sbin:/usr/bin:/sbin:/bin"

    for step in action["runs"]["steps"]:
        if "--noprofile" not in step.get("shell", ""):
            continue

        env = step.get("env", {})
        lines = step["run"].splitlines()
        if len(lines) == 1:
            assert lines[0].startswith(f"{safe_path} /usr/bin/"), step["name"]
            continue

        assert {name: env.get(name) for name in ("BASHOPTS", "SHELLOPTS", "PS4")} == {
            "BASHOPTS": "",
            "SHELLOPTS": "",
            "PS4": "",
        }, step["name"]
        assert lines[0] == "set +x", step["name"]
        assert lines[1] == safe_path or lines[1].startswith("/usr/bin/env -i"), step[
            "name"
        ]


def test_experimental_memory_gist_sync_cannot_replace_the_agent_verdict() -> None:
    action = YAML(typ="safe", pure=True).load(
        (REPO_ROOT / "claude" / "action.yaml").read_text()
    )
    steps = {step["name"]: step for step in action["runs"]["steps"]}

    assert action["inputs"]["memory_gist"]["default"] == "false"
    assert steps["Restore experimental memory Gist"]["continue-on-error"] is True
    assert steps["Save experimental memory Gist"]["continue-on-error"] is True
    assert (
        steps["Remove experimental memory Gist working copy"]["continue-on-error"]
        is True
    )
    assert (
        steps["Save experimental memory Gist"]["if"]
        == "always() && steps.auto_memory.outcome == 'success' && "
        "steps.claude.outputs.sandbox_reaped == 'true'"
    )
    restore = steps["Restore experimental memory Gist"]["run"]
    save = steps["Save experimental memory Gist"]["run"]
    assert 'gist_memory.py" \\\n  restore;' in restore
    assert 'gist_memory.py" \\\n  save;' in save


def test_memory_gist_save_reads_nothing_the_dispose_step_deleted() -> None:
    """The save's inputs are made outside the home and the runtime container.

    The dispose step deletes the runtime container, private directory and all,
    right after the agent is reaped and before the save runs. The view shows the
    agent the runner's home as its own, so `RUNNER_TEMP` would hand it the key.
    """
    action = YAML(typ="safe", pure=True).load(
        (REPO_ROOT / "claude" / "action.yaml").read_text()
    )
    steps = {step["name"]: step for step in action["runs"]["steps"]}
    restore = steps["Restore experimental memory Gist"]["run"]

    assert "memory_dir=$(/usr/bin/mktemp -d /var/tmp/tend-auto-memory." in restore
    assert "key_file=$(/usr/bin/mktemp /var/tmp/tend-auto-memory-key." in restore


def test_uv_build_range_admits_the_pinned_uv() -> None:
    # uv only *warns* when `build-system.requires` doesn't contain the uv
    # running the build, so a stale range survives every release and every
    # `uv sync` without failing anything. The harness `uv_version` inputs are
    # the repo's statement of which uv is current — the weekly sweep moves them
    # to the latest release — so tying the range to them makes that sweep carry
    # the backend along instead of leaving it for someone to notice in the noise.
    # Both operands are in-repo, so this can only go red on a bump, never on
    # the day astral publishes something.
    requires = tomllib.loads((REPO_ROOT / "generator" / "pyproject.toml").read_text())[
        "build-system"
    ]["requires"]
    backends = [
        Requirement(r)
        for r in requires
        if canonicalize_name(Requirement(r).name) == "uv-build"
    ]
    assert len(backends) == 1, f"expected one uv_build requirement, got: {requires}"

    uv_versions = [
        YAML(typ="safe", pure=True).load(
            (REPO_ROOT / harness / "action.yaml").read_text()
        )["inputs"]["uv_version"]["default"]
        for harness in ("claude", "codex")
    ]
    assert uv_versions[0] == uv_versions[1], f"harness uv pins differ: {uv_versions}"
    uv_version = uv_versions[0]

    assert Version(uv_version) in backends[0].specifier, (
        f"build-system.requires pins `{backends[0]}`, which does not contain the "
        f"uv this repo pins ({uv_version}); `uv build` warns and the wheel is "
        "built by a backend a release older than the uv building it"
    )


def test_generated_workflow_uv_uses_the_action_pin() -> None:
    action = YAML(typ="safe", pure=True).load(
        (REPO_ROOT / "claude" / "action.yaml").read_text()
    )

    assert UV_VERSION == action["inputs"]["uv_version"]["default"]


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_privileged_sandbox_launch_scrubs_consumer_runtime_configuration(
    harness: str,
) -> None:
    action = YAML(typ="safe", pure=True).load(
        (REPO_ROOT / harness / "action.yaml").read_text()
    )
    step = next(
        step
        for step in action["runs"]["steps"]
        if step.get("name") == "Set up credential-isolation sandbox"
    )
    run = step["run"]

    assert step["env"]["BASH_ENV"] == ""
    assert step["env"]["BASHOPTS"] == ""
    assert step["env"]["SHELLOPTS"] == ""
    assert step["env"]["PS4"] == ""
    assert run.startswith("set +x\n")
    assert "/usr/bin/env -i" in run
    assert "UV_NO_CONFIG=1" in run
    assert "PYTHONNOUSERSITE=1" in run
    assert "--no-python-downloads --python /usr/bin/python3 --script" in run


# Set to neutralize this step's own shell, not to reach the script: `env -i`
# drops them by construction.
SHELL_HARDENING = frozenset({"BASH_ENV", "BASHOPTS", "SHELLOPTS", "PS4"})


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_privileged_sandbox_launch_forwards_every_configured_value(
    harness: str,
) -> None:
    """`env:` and the `env -i` argv are two lists that have to agree.

    A value reaches `setup_sandbox.py` only when both name it. Nothing else catches a value
    added to one list alone: neither action.yaml is linted or run here, and the
    hosted sandbox test supplies the script's environment itself — so the
    mismatch would first run in a consumer's job after a release.
    """
    action = YAML(typ="safe", pure=True).load(
        (REPO_ROOT / harness / "action.yaml").read_text()
    )
    step = next(
        step
        for step in action["runs"]["steps"]
        if step.get("name") == "Set up credential-isolation sandbox"
    )
    forwarded = set(re.findall(r'(\w+)="\$\1"', step["run"]))

    assert set(step["env"]) - SHELL_HARDENING <= forwarded


def test_codex_actions_pin_the_same_cli_version() -> None:
    versions = {
        action: YAML(typ="safe", pure=True).load((REPO_ROOT / action).read_text())[
            "inputs"
        ]["codex_version"]["default"]
        for action in ("codex/action.yaml", "codex/refresh/action.yaml")
    }

    assert len(set(versions.values())) == 1, f"Codex CLI pins differ: {versions}"


# Every `${{ github.action_path }}/…` reference in the composite actions.
# Nothing else reads them: the pre-commit actionlint hook is pinned to
# ^.github/workflows/, so neither action.yaml is linted at all, and no workflow
# here consumes the actions with `uses: ./` — they pin a released ref, so an
# edited body first runs in a consumer's job. A path that resolves nowhere fails
# its step, for every consumer, on the first run after a release.
ACTION_PATH_REF = re.compile(r"\$\{\{\s*github\.action_path\s*\}\}/?([^\s\"')]*)")


COMPOSITE_ACTIONS = (
    "claude/action.yaml",
    "codex/action.yaml",
    "codex/refresh/action.yaml",
)


@pytest.mark.parametrize("action", COMPOSITE_ACTIONS)
def test_credential_actions_cache_only_in_restricted_mode(action: str) -> None:
    """Only restricted mode trusts main-branch code that can publish caches."""
    data = YAML(typ="safe").load((REPO_ROOT / action).read_text())
    restores = [
        step
        for step in data["runs"]["steps"]
        if step.get("uses", "").split("@", 1)[0].casefold()
        in {"actions/cache", "actions/cache/restore"}
    ]
    if action != "codex/refresh/action.yaml":
        assert restores, f"{action} must retain caching in restricted mode"
        assert all(
            step.get("if") == "inputs.merge == 'restricted'" for step in restores
        )


@pytest.mark.parametrize("action", COMPOSITE_ACTIONS)
def test_action_path_references_resolve(action: str) -> None:
    action_path = REPO_ROOT / action
    body = action_path.read_text()
    matched = ACTION_PATH_REF.findall(body)

    assert matched, f"{action}: no github.action_path references"
    missing = [
        ref
        for ref in sorted(set(matched))
        if not (action_path.parent / ref).resolve().exists()
    ]
    assert not missing, f"{action} references nothing at: {missing}"


# Every `references/<file>` a skill cites. A reference holds text one skill owns,
# and the citation is the only path to it: one pointing nowhere means the session
# reads no file and goes ahead without it, with nothing failing. The path is
# written from the owning skill's directory, with that skill in front of it where
# another skill's text does the citing (``/tend-ci-runner:review`'s
# `references/approving.md``).
SKILL_REFERENCE = re.compile(
    r"(?:`/[a-z-]+:(?P<skill>[a-z-]+)`'s\s+)?`?"
    r"references/(?P<file>[\w.-]+\.\w+)`?"
)
# A bare filename inside a `references/` directory. Its own neighbour is still
# cited `references/<file>`, so that one citation form reads the same wherever
# it appears and moves with the text that carries it.
BARE_MD = re.compile(r"`(?P<file>[\w-]+\.md)`")
# Names that are bare wherever they appear: the files a repo carries at its own
# root, and `SKILL.md`, which reference files name literally (a path in a shell
# recipe, the file a new skill starts as). Citing a *rule* in the skill's own
# `SKILL.md` still goes by section and skill, which no regex can tell from the
# literal mentions — AGENTS.md's Authoring skills carries that half.
ROOT_FILES = {
    "AGENTS.md",
    "CLAUDE.local.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "README.md",
    "SKILL.md",
}


def test_skill_reference_citations_resolve() -> None:
    """Every `references/` citation resolves, and every reference file is cited.

    A citation naming a file that isn't there loads nothing; a file nothing
    names loads in no session. Both ship silently, so the check runs in each
    direction.
    """
    skill_dirs = [
        *(REPO_ROOT / "plugins").glob("*/skills/*"),
        *(REPO_ROOT / ".claude" / "skills").glob("*"),
    ]
    by_name = {d.name: d for d in skill_dirs}
    on_disk = {path for d in skill_dirs for path in (d / "references").glob("*.md")}
    resolved: set[Path] = set()
    cited, broken = 0, []

    for skill in skill_dirs:
        # Symlinks stay in: `shared/` is reachable only through them.
        for path in skill.glob("**/*.md"):
            text = path.read_text()

            for match in SKILL_REFERENCE.finditer(text):
                cited += 1
                named = match.group("skill")
                if named and named not in by_name:
                    broken.append(
                        f"{path.relative_to(REPO_ROOT)}: no skill named `{named}`"
                    )
                    continue
                owner = by_name[named] if named else skill
                target = owner / "references" / match.group("file")
                if target.exists():
                    resolved.add(target)
                else:
                    broken.append(
                        f"{path.relative_to(REPO_ROOT)}: {match.group().strip()}"
                    )

            if path.parent.name == "references":
                for match in BARE_MD.finditer(text):
                    name = match.group("file")
                    if name not in ROOT_FILES:
                        broken.append(
                            f"{path.relative_to(REPO_ROOT)}: `{name}` — cite it "
                            f"as `references/{name}`, or add it to ROOT_FILES "
                            f"if the repo carries it at its root"
                        )

    assert cited, "no references/ citations found — did the skill layout move?"
    assert not broken, "references named but absent:\n" + "\n".join(broken)

    orphans = sorted(str(path.relative_to(REPO_ROOT)) for path in on_disk - resolved)
    assert not orphans, (
        "reference files no skill cites, so no session loads them:\n"
        + "\n".join(orphans)
    )


# Inline `run:` bodies in the composite actions. Nothing else lints them:
# actionlint only reads workflow files (it parses an action.yaml as a malformed
# workflow — "jobs section is missing"), and the shellcheck hook's `files:`
# regex covers the standalone step scripts, not `claude/` or `codex/`.
# Replace `${{ … }}` with an opaque shell variable before checking composite
# bodies. A literal placeholder would make shellcheck report on the replacement
# instead of the code (`[ -z "literal" ]` → SC2157 "always false").
GHA_EXPR = re.compile(r"\$\{\{.*?\}\}", re.DOTALL)


@pytest.mark.parametrize("action", COMPOSITE_ACTIONS)
def test_inline_run_bodies_pass_shellcheck(action: str) -> None:
    """Hold inline step bodies to the same shellcheck the step scripts get.

    Severity matches the shellcheck hook in .pre-commit-config.yaml.
    """
    shellcheck = shutil.which("shellcheck")
    assert shellcheck, "install shellcheck (preinstalled on CI runners)"

    doc = YAML(typ="safe", pure=True).load((REPO_ROOT / action).read_text())
    steps = [step for step in doc["runs"]["steps"] if "run" in step]
    assert steps, f"{action}: no inline `run:` bodies found — did the schema move?"
    # `-s bash` below is a claim about the body, not a default: a step that
    # pinned `shell: sh` would have its bashisms checked as valid.
    not_bash = [s.get("name") for s in steps if "bash" not in s.get("shell", "")]
    assert not not_bash, f"{action}: not shellcheck-able as bash: {not_bash}"

    findings = []
    for step in steps:
        name, body = step.get("name", "<unnamed>"), step["run"]
        result = subprocess.run(
            [shellcheck, "-S", "warning", "-s", "bash", "-"],
            input=GHA_EXPR.sub("${_GHA_EXPR}", body),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            findings.append(f"--- {action} :: {name}\n{result.stdout}")

    assert not findings, "\n".join(findings)


# Anchoring on `${{ … }}` would match the bare reference alone, letting
# `${{ inputs.x || '' }}` and `${{ format('{0}', inputs.x) }}` through. Any
# `inputs.<name>` in a `run:` body is necessarily a GHA expression — bash has no
# such syntax — so the unanchored match is both simpler and strictly broader.
# (`inputs['x']` index syntax is missed either way.)
INPUT_REF = re.compile(r"inputs\.([A-Za-z0-9_]+)")


@pytest.mark.parametrize("action", COMPOSITE_ACTIONS)
def test_inputs_reach_run_bodies_through_env(action: str) -> None:
    """An input must not be interpolated into an inline `run:` body.

    GitHub substitutes `${{ … }}` into the script *text* before bash parses it,
    so a value carrying a quote or `$(…)` stops being a string and becomes
    script executing as the runner user — which holds the real PAT and, under
    codex, the model key. Through `env:` the value is passed to the process,
    never to the parser. Nothing else catches this: shellcheck sees the
    placeholder the sibling test substitutes in, and actionlint does not read
    action.yaml at all.

    Every input already arrives this way, so the rule is a flat
    ban rather than a list of which values are secret enough to deserve it.
    """
    doc = YAML(typ="safe", pure=True).load((REPO_ROOT / action).read_text())

    inlined = [
        f"{step.get('name', '<unnamed>')} inlines inputs.{name}"
        for step in doc["runs"]["steps"]
        if "run" in step
        for name in sorted(set(INPUT_REF.findall(step["run"])))
    ]
    assert not inlined, (
        f"{action}: pass these through the step's `env:` instead of `${{{{ }}}}` "
        f"in the body: {inlined}"
    )


def test_codex_action_passes_selected_auth_mode_to_runner() -> None:
    doc = YAML(typ="safe", pure=True).load(
        (REPO_ROOT / "codex" / "action.yaml").read_text()
    )
    run = next(step for step in doc["runs"]["steps"] if step.get("name") == "Run Codex")

    assert run["env"]["AUTH_MODE"] == "${{ steps.codex_auth.outputs.mode }}"
    assert run["run"].endswith('/launch_agent.py"')


def test_codex_refresher_keeps_the_secret_writer_pat_out_of_the_model_step() -> None:
    doc = YAML(typ="safe", pure=True).load(
        (REPO_ROOT / "codex" / "refresh" / "action.yaml").read_text()
    )
    steps = {step["name"]: step for step in doc["runs"]["steps"]}

    run = steps["Run Codex refresh"]
    assert run["continue-on-error"] is True
    assert set(run["env"]) == {"CODEX_HOME"}
    publish = steps["Publish refreshed auth"]
    assert publish["if"].startswith("always()")
    assert publish["env"]["GH_TOKEN"] == "${{ inputs.refresh_pat }}"
    assert publish["env"]["CODEX_OUTCOME"] == "${{ steps.codex.outcome }}"


def test_bundled_runner_instructions_never_return_with_cd_dash() -> None:
    """`cd -` cannot bring a session back to where a recipe started.

    It restores `$OLDPWD`, which is whatever the last `cd` left — after a
    recipe's second `cd` that is the first `cd`'s target, not the checkout.
    The worktree recipes end by deleting the directory they moved into, so a
    session that followed one is left with no working directory and every
    later command fails. A recipe that has to change directory does it in a
    subshell, which never moves the session's own cwd.
    """
    runner = REPO_ROOT / "plugins" / "tend-ci-runner"
    cd_dash = re.compile(r"(?<![\w-])cd\s+-(?![\w-])")
    offenders = sorted(
        f"{path.relative_to(REPO_ROOT)}:{number}"
        for path in runner.rglob("*")
        if path.suffix in {".md", ".py", ".sh"}
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if cd_dash.search(line)
    )

    assert offenders == []


def test_runner_helper_directory_is_python_only() -> None:
    """Substantial runner behavior belongs in tested Python, not shell helpers."""
    scripts = REPO_ROOT / "plugins" / "tend-ci-runner" / "scripts"

    assert not sorted(scripts.glob("*.sh"))


def test_review_reviewers_sweeps_only_known_consumers() -> None:
    """The matrix is a hand-picked subset — the consumers nobody here
    maintains — so nothing enforces that it covers `consumers.json`. What it
    must not carry is a repo that is no longer a consumer at all: that leg
    fails remotely, on a missing `.config/tend.yaml`, rather than here.
    """
    workflow = YAML(typ="safe", pure=True).load(
        (REPO_ROOT / ".github" / "workflows" / "review-reviewers.yaml").read_text()
    )
    matrix = workflow["jobs"]["review-reviewers"]["strategy"]["matrix"]["repo"]
    consumers = {
        entry["repo"]
        for entry in json.loads((REPO_ROOT / "data" / "consumers.json").read_text())
    }

    unknown = sorted(set(matrix) - consumers)
    assert not unknown, f"review-reviewers targets are not consumers: {unknown}"


def test_every_workflow_pins_the_same_tend_release() -> None:
    """`init` rewrites only the generated `tend-*.yaml` files.

    Every other workflow keeps whatever `max-sixty/tend/...` ref it was last
    given by hand, so each release leaves it a version behind until someone
    restamps it. The nightly sweep does the restamping; this is what decides
    whether it is needed.
    """
    refs = {
        ref
        for path in (REPO_ROOT / ".github" / "workflows").glob("*.y*ml")
        for ref in re.findall(r"max-sixty/tend/[\w./-]+@[^\s\"']+", path.read_text())
    }
    assert refs

    assert len({ref.split("@")[1] for ref in refs}) == 1, (
        f"workflows pin more than one tend release: {sorted(refs)}. "
        "Restamp the hand-maintained workflows onto the generated files' ref."
    )


# A bundled skill invoked as a slash command, or named by the system prompt's
# `${SKILL:<name>}` placeholder, which `_prompt.py` renders into that same
# invocation for whichever harness is running. `<name>` and `NAME` placeholders
# don't match, so prose about the citation form isn't read as a citation.
PLUGIN_SKILL = re.compile(r"(?:/tend-ci-runner:|\$\{SKILL:)(?P<skill>[a-z0-9-]+)")


def test_plugin_skill_citations_resolve() -> None:
    """Every `/tend-ci-runner:<name>` a shipped file cites exists.

    The skill listing every session carries is the plugin's index of its
    per-action skills, and a citation is how the text that needs one reaches it
    from the step that acts. A citation left behind by a rename resolves to
    nothing: the session loads no skill and goes ahead without the rules, with
    nothing failing. `CHANGELOG.md` is excluded — its entries are published
    release notes and name the skills as they stood at the time.
    """
    names = {
        path.parent.name
        for path in (REPO_ROOT / "plugins" / "tend-ci-runner" / "skills").glob(
            "*/SKILL.md"
        )
    }
    cited, broken = 0, []

    for path in sorted(REPO_ROOT.rglob("*.md")):
        if ".git" in path.parts or path.name == "CHANGELOG.md":
            continue
        for match in PLUGIN_SKILL.finditer(path.read_text()):
            cited += 1
            if match.group("skill") not in names:
                broken.append(f"{path.relative_to(REPO_ROOT)}: {match.group()}")

    assert cited, "no plugin-skill citations found — did the skill layout move?"
    assert not broken, "skills cited but absent:\n" + "\n".join(broken)


# What Codex 0.155.0 leaves each description, measured against the installed
# plugin at SKILLS_MEASURED_AT skills: the listing shares one budget across them,
# so a longer description is cut mid-sentence and every session reads a trigger
# that stops partway. The share falls as skills are added, and several
# descriptions sit within a few characters of the ceiling, so the count is pinned
# below — adding a skill means re-measuring, not raising it. The count spans both
# plugins, because the install carries both and they share the one budget.
#
# To re-measure, install both plugins from this checkout into a throwaway Codex
# home and run any prompt:
#
#     export CODEX_HOME=$(mktemp -d)
#     codex login
#     codex plugin marketplace add "$PWD"
#     codex plugin add tend-ci-runner@tend; codex plugin add install-tend@tend
#     codex exec --skip-git-repo-check -s read-only "Quote your skills listing."
#     rm -rf "$CODEX_HOME"
#
# Log in afresh rather than copying `~/.codex/auth.json` in: a token refresh
# inside the throwaway home would rotate the refresh-token chain your own login
# depends on.
#
# Codex does not say when it shortens a description: 0.156.0 removed the
# "Skill descriptions were shortened to fit the skills context budget" warning,
# and the budget path cuts at a character index with no ellipsis. So the listing
# the model quotes back is the whole signal — a description that stops
# mid-sentence was cut, and its length is the share each one gets. Tighten the
# descriptions and re-run until none is cut. Codex still warns when a skill is
# left out of the listing entirely, which is the budget exhausted rather than
# shared thin.
DESCRIPTION_BUDGET = 130
SKILLS_MEASURED_AT = 24


def test_skill_frontmatter_is_loadable() -> None:
    """Installed skill frontmatter parses and fits the discovery listing.

    The harness reads `name` and `description` out of this block to build the
    listing that is the plugin's index. An unquoted `: ` inside a description
    makes the block invalid YAML, and a description over the budget is truncated
    in the listing — either way the skill stops being findable at the moment it
    is needed, with nothing failing. Only bundled CI skills must be internal.
    """
    yaml = YAML(typ="safe", pure=True)
    broken = []
    installed = sorted((REPO_ROOT / "plugins").glob("*/skills/*/SKILL.md"))
    assert len(installed) == SKILLS_MEASURED_AT, (
        f"{len(installed)} skills across both plugins, not the "
        f"{SKILLS_MEASURED_AT} the budget was measured at — re-measure the "
        "share against the install, and move DESCRIPTION_BUDGET with the count"
    )

    for path in installed:
        name = path.relative_to(REPO_ROOT)
        head, _, _ = path.read_text().removeprefix("---\n").partition("\n---\n")
        try:
            front = yaml.load(head)
        except YAMLError as error:
            broken.append(
                f"{name}: frontmatter is not YAML ({error.__class__.__name__})"
            )
            continue
        if not isinstance(front, dict):
            broken.append(f"{name}: no `---` frontmatter block above the body")
            continue
        if front.get("name") != path.parent.name:
            broken.append(f"{name}: `name: {front.get('name')}` isn't the directory")
        if "tend-ci-runner" in path.parts and not (front.get("metadata") or {}).get(
            "internal"
        ):
            broken.append(f"{name}: bundled skills are `metadata: internal: true`")
        description = front.get("description", "")
        if not description:
            broken.append(f"{name}: no description, so the listing carries no trigger")
        elif len(description) > DESCRIPTION_BUDGET:
            broken.append(
                f"{name}: description is {len(description)} chars, over the "
                f"{DESCRIPTION_BUDGET} the listing shows"
            )

    assert not broken, "unloadable skill frontmatter:\n" + "\n".join(broken)


def test_every_workflow_prompt_names_a_skill_that_exists() -> None:
    """A prompt's first line invokes a skill; a rename leaves it pointing nowhere.

    `/<plugin>:<name>` resolves in that bundled plugin and `/<name>` in this
    repo's own `.claude/skills/` — which is how the hand-maintained
    `review-reviewers.yaml` reaches tend's overlay copy. Under `harness: codex`
    the generator writes the same invocation as `$<name>` (`default_prompt`).
    `tend-mention` is the one agent-invoking workflow whose prompt opens with an
    expression instead, because it names no skill at all (TODO.md).
    """
    yaml = YAML(typ="safe", pure=True)
    checked = []

    for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.y*ml")):
        workflow = yaml.load(path.read_text())
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                prompt = step.get("with", {}).get("prompt")
                if not prompt:
                    continue
                first = prompt.strip().split()[0]
                if first.startswith("${{"):
                    continue
                if first.startswith("$"):
                    # Codex mentions a bundled skill as `$NAME` (`default_prompt`).
                    plugin, skill = "tend-ci-runner", first.lstrip("$")
                else:
                    assert first.startswith("/"), (
                        f"{path.name}'s prompt opens with `{first}`, neither a "
                        "slash command nor a Codex skill mention"
                    )
                    plugin, _, skill = first.lstrip("/").rpartition(":")
                target = (
                    REPO_ROOT / "plugins" / plugin / "skills" / skill
                    if plugin
                    else REPO_ROOT / ".claude" / "skills" / skill
                )
                assert (target / "SKILL.md").is_file(), (
                    f"{path.name} invokes `{first}`, which is not a skill at "
                    f"{target.relative_to(REPO_ROOT)}"
                )
                checked.append(skill)

    assert "review-reviewers" in checked, (
        "review-reviewers.yaml stopped naming its skill"
    )


def _prompt_module():
    spec = importlib.util.spec_from_file_location(
        "tend_prompt", REPO_ROOT / "shared/steps/_prompt.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_skill_prefixes_match_the_generator() -> None:
    """One mapping from skill name to invocation, asserted across two deliverables.

    `shared/steps/_prompt.py` renders `${SKILL:<name>}` in the system prompt at
    runtime; `Config.default_prompt` writes the same invocation into generated
    workflow prompts. The generator is not installed on the runner, so neither
    can import the other.
    """
    prompt = _prompt_module()

    for harness, prefix in prompt.SKILL_PREFIX.items():
        cfg = Config(
            bot_name="bot",
            default_branch="main",
            protected_branches=[],
            harness=harness,
            model="opus",
            effort="",
            setup=[],
            workflows={},
        )
        assert cfg.default_prompt("run-tend") == f"{prefix}run-tend"
    assert set(prompt.SKILL_PREFIX) == KNOWN_HARNESSES


def test_shipped_prompt_skill_tokens_resolve_to_a_bundled_skill() -> None:
    """Neither way of naming the wrong skill fails until a session is running.

    A token that renames or misspells a skill stays well-formed, renders in both
    syntaxes, and opens every shipped session with a load of something that does
    not exist — so resolve the name on disk, as
    `test_every_workflow_prompt_names_a_skill_that_exists` does for the
    generator's half. A token malformed enough to miss the pattern survives
    rendering instead, reaching the model verbatim.
    """
    prompt = _prompt_module()
    files = [REPO_ROOT / "shared/system-prompt.md", REPO_ROOT / "codex/agents-tail.md"]

    for path in files:
        text = path.read_text()
        for name in prompt.SKILL_REF.findall(text):
            skill = REPO_ROOT / "plugins/tend-ci-runner/skills" / name / "SKILL.md"
            assert skill.is_file(), (
                f"{path.relative_to(REPO_ROOT)} invokes `{name}`, which is not "
                f"a skill at {skill.relative_to(REPO_ROOT)}"
            )
        for harness in prompt.SKILL_PREFIX:
            rendered = prompt.render(
                text, bot_name="bot", merge="restricted", harness=harness
            )
            assert "${SKILL" not in rendered, (
                f"{path.relative_to(REPO_ROOT)} has a malformed skill token; "
                "it must read ${SKILL:<lowercase-skill-name>}"
            )


@pytest.mark.parametrize("harness", sorted(KNOWN_HARNESSES))
def test_report_failure_is_told_the_running_version(harness: str) -> None:
    """`report_failure.py` cannot read the pin it is running at.

    `github.action_ref` and `github.action_repository` resolve in a composite
    step's `env:` and not inside its `run:` body, where they expand to the
    empty string rather than failing (actions/runner#2473). So dropping either
    from either harness leaves the outage tracker silently unable to name a
    stale pin as the remedy, with nothing else red.
    """
    action = YAML(typ="safe", pure=True).load(
        (REPO_ROOT / harness / "action.yaml").read_text()
    )
    steps = {step["name"]: step for step in action["runs"]["steps"]}
    env = steps["Report failure"]["env"]

    assert env["TEND_ACTION_REF"] == "${{ github.action_ref }}"
    assert env["TEND_ACTION_REPOSITORY"] == "${{ github.action_repository }}"


def test_run_tend_names_every_pinned_instruction_path() -> None:
    """`run-tend`'s restore list covers every path the restore actually pins.

    The dangerous direction is a pinned path the skill omits: the session reads
    the worktree as the PR's own version, and the "never stage one of these"
    rule doesn't reach it, so a `git add` commits the base content back over
    the PR's edit. Nothing else pairs the two — the restore is shell, the rule
    is prose.
    """
    script = (REPO_ROOT / "shared/steps/lib/pin-instruction-paths.sh").read_text()
    declaration = re.search(r"^INSTRUCTION_PATHSPECS=\((.*)\)$", script, re.MULTILINE)
    assert declaration, "INSTRUCTION_PATHSPECS is no longer one array literal"
    pinned = {
        spec.strip("'").removeprefix(":(glob)**/").removesuffix("/**")
        for spec in declaration.group(1).split()
    }

    skill = (REPO_ROOT / "plugins/tend-ci-runner/skills/run-tend/SKILL.md").read_text()
    heading = "## Instruction paths read as the base version on a PR"
    assert heading in skill, f"{heading!r} was renamed — repoint this test"
    section = skill.split(heading, 1)[1].split("\n## ", 1)[0]

    missing = sorted(
        name
        for name in pinned
        if f"`{name}`" not in section and f"`{name}/`" not in section
    )
    assert not missing, (
        "pinned by restore-sensitive-config.sh but absent from the skill's "
        f"list, so a session stages the base version over the PR's: {missing}"
    )
