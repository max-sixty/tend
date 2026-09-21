/** Launch exactly one Tend lifecycle through Anthropic's Sandbox Runtime. */

import { randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import { access } from "node:fs/promises";
import { constants } from "node:os";
import { dirname } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is unset`);
  return value;
}

function absolute(name) {
  const value = required(name);
  if (!value.startsWith("/")) throw new Error(`${name} must be absolute`);
  return value;
}

function describe(error) {
  return error instanceof Error ? error.message : error;
}

function quote(value) {
  return `'${value.replaceAll("'", `'"'"'`)}'`;
}

// SRT gives socat 600 ms total to bind the bridge sockets
// (`initializeLinuxNetworkBridge` probes five times on an `i * 100` ms
// backoff), and Tend's config is the expensive branch: an external
// `httpProxyPort` with no `socksProxyPort` makes SRT allocate its own mux
// port for SOCKS, so the two ports differ, a second socat is spawned, and
// both sockets have to appear inside that one budget. A runner slow to start
// them loses the whole job before the agent exists. Retrying is clean here:
// SRT clears its own state on an initialization error so the call can be
// re-entered, each attempt names fresh random socket paths, and nothing has
// run in the sandbox yet, so there is no partial work to reconcile.
const LAUNCH_ATTEMPTS = 3;

async function initializeSandbox(SandboxManager, config) {
  for (let attempt = 1; ; attempt++) {
    try {
      await SandboxManager.initialize(config, undefined, false);
      return;
    } catch (error) {
      if (attempt >= LAUNCH_ATTEMPTS) throw error;
      console.error(
        `tend sandbox runtime: launch attempt ${attempt} failed (${describe(error)}); retrying`,
      );
      // SRT's own error path calls reset() and lets it reject unobserved.
      // Await one here so the failed attempt's proxy servers are closed
      // before the next allocates its own, but keep a cleanup failure from
      // replacing the launch error this loop exists to report.
      await SandboxManager.reset().catch((cleanup) => {
        console.error(
          `tend sandbox runtime: cleanup after attempt ${attempt} failed (${describe(cleanup)})`,
        );
      });
      await new Promise((resolve) => setTimeout(resolve, attempt * 1000));
    }
  }
}

async function main() {
  if (process.platform !== "linux") throw new Error("Tend SRT requires Linux");
  const entry = absolute("TEND_SRT_ENTRY");
  const seccomp = absolute("TEND_SRT_SECCOMP");
  const lifecycle = absolute("TEND_LIFECYCLE");
  const workspace = absolute("GITHUB_WORKSPACE");
  const agentHome = absolute("AGENT_HOME");
  const agentTmpDir = absolute("TMPDIR");
  const runnerHome = absolute("TEND_RUNNER_HOME");
  const autoMemory = process.env.TEND_AUTO_MEMORY_DIRECTORY;
  if (autoMemory && !autoMemory.startsWith("/")) {
    throw new Error("TEND_AUTO_MEMORY_DIRECTORY must be absolute");
  }

  for (const path of [entry, seccomp, lifecycle, workspace, agentHome]) {
    await access(path);
  }

  // SRT resolves its mandatory write protections (shell rc files, `.gitconfig`,
  // `.gitmodules`, `.mcp.json`, `.vscode`, `.claude/commands`, `.git/hooks`, …)
  // against THIS process's cwd, and applies them inside `allowWrite` only: a
  // protected path that exists is re-bound read-only, one that doesn't gets a
  // `/dev/null` bind. They exist to stop a sandboxed write from being run later
  // by something unsandboxed, and nothing outside this process tree runs what
  // the agent writes under the home: every write there lands in the view's
  // upper layer, which only this tree sees and the dispose step deletes. Resolved
  // against the checkout they leave character devices git refuses to add and
  // tracked paths neither the agent nor the pull request's checkout can change;
  // against the home, they mask `~/.gitconfig`. This launcher's own staged
  // directory is outside every `allowWrite` path, so resolved there they emit
  // nothing. The lifecycle's cwd is the `spawn` below's, not this one.
  process.chdir(dirname(fileURLToPath(import.meta.url)));

  const { SandboxManager } = await import(`file://${entry}`);
  // With filesystem isolation on, SRT sets TMPDIR in the child environment to
  // CLAUDE_CODE_TMPDIR or its own /tmp/claude default, whichever it finds,
  // overriding the agent environment file. Tend's scratch directory is the one
  // the supervisor reads the step summary from and the one the shipped skills
  // name, so hand SRT that path rather than letting it choose.
  process.env.CLAUDE_CODE_TMPDIR = agentTmpDir;
  const command = `/usr/bin/python3 -E -s ${quote(lifecycle)}`;
  const config = {
    network: {
      // Tend's existing proxy is deliberately the HTTP policy/broker. Empty
      // here still enables SRT's isolated network namespace; the external
      // proxy owns destination handling, as required by SRT's API.
      allowedDomains: [],
      deniedDomains: [],
      httpProxyPort: Number(required("TEND_PROXY_PORT")),
      allowLocalBinding: false,
    },
    filesystem: {
      // Denying /tmp makes SRT mount a private tmpfs there: writable scratch
      // for tools that hard-code /tmp. Nothing reaches the runner's /tmp (a
      // cache action there would save it) because `enter_view.py` gives this
      // namespace an empty one too, so SRT's default `/tmp/claude` bind finds
      // nothing of the host's. SRT's own sockets follow TMPDIR, which points
      // into the sandbox home, so neither tmpfs covers them.
      denyRead: ["/tmp"],
      allowRead: [],
      // The runner's home is the copy-on-write view `enter_view.py` mounted;
      // bwrap binds whatever the parent namespace has at this path.
      allowWrite: [runnerHome, agentHome, ...(autoMemory ? [autoMemory] : [])],
      denyWrite: [],
      allowGitConfig: true,
    },
    ripgrep: { command: "/usr/bin/rg" },
    seccomp: { applyPath: seccomp },
    bwrapPath: "/usr/bin/bwrap",
    socatPath: "/usr/bin/socat",
    git: { safeDirectories: [workspace] },
  };

  let child;
  let signal;
  const forward = (name) => {
    signal = name;
    child?.kill(name);
  };
  process.on("SIGINT", () => forward("SIGINT"));
  process.on("SIGTERM", () => forward("SIGTERM"));

  const token = `tend-${randomUUID()}`;
  let commandsStopped = false;
  try {
    await initializeSandbox(SandboxManager, config);
    const dependencies = await SandboxManager.checkDependenciesAsync({
      command: "/usr/bin/rg",
    });
    if (dependencies.errors.length || dependencies.warnings.length) {
      throw new Error(
        `SRT dependency check failed: ${[
          ...dependencies.errors,
          ...dependencies.warnings,
        ].join(", ")}`,
      );
    }
    const wrapped = await SandboxManager.wrapWithSandboxArgv(
      command,
      "/usr/bin/bash",
      undefined,
      undefined,
      workspace,
      { commandId: "tend-agent-lifecycle", commandText: command },
    );
    console.log(`::stop-commands::${token}`);
    commandsStopped = true;
    child = spawn(wrapped.argv[0], wrapped.argv.slice(1), {
      cwd: workspace,
      env: { ...process.env, ...wrapped.env },
      stdio: ["ignore", "pipe", "pipe"],
    });
    child.stdout.pipe(process.stdout);
    child.stderr.pipe(process.stderr);
    const code = await new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("close", (status, childSignal) => {
        if (childSignal) resolve(128 + (constants.signals[childSignal] ?? 0));
        else resolve(status ?? 1);
      });
    });
    return signal ? 128 + (constants.signals[signal] ?? 0) : code;
  } finally {
    if (child && child.exitCode === null && child.signalCode === null) {
      child.kill("SIGKILL");
    }
    try {
      await SandboxManager.reset();
    } finally {
      if (commandsStopped) console.log(`::${token}::`);
    }
  }
}

try {
  process.exitCode = await main();
} catch (error) {
  console.error(`tend sandbox runtime: ${describe(error)}`);
  process.exitCode = 1;
}
