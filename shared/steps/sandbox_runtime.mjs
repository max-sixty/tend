/** Launch exactly one Tend lifecycle through Anthropic's Sandbox Runtime. */

import { randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import { access } from "node:fs/promises";
import { constants } from "node:os";
import process from "node:process";

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

function quote(value) {
  return `'${value.replaceAll("'", `'"'"'`)}'`;
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
      // for tools that hard-code /tmp, and nothing reaches the runner's /tmp
      // (a cache action there would save it). SRT's own sockets follow TMPDIR,
      // which points into the sandbox home, so the tmpfs covers none of them.
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
    await SandboxManager.initialize(config, undefined, false);
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
  console.error(`tend sandbox runtime: ${error instanceof Error ? error.message : error}`);
  process.exitCode = 1;
}
