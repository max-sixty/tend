/** Run each eval as a fresh Codex task in its own profile and workspace.
 * Promptfoo owns execution; this adapter isolates attempts and reads the actual
 * artifact before retiring the workspace. Trajectory cases also expose the
 * current attempt's SDK events and repository changes to the separate judge.
 * An explicit permission profile confines commands to staged evidence and
 * minimal runtime paths, with network access disabled. Historical evidence
 * is retained data, not a GitHub API.
 */
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

module.exports = class CodexProvider {
  constructor(options) {
    this.config = options.config;
    this.providerId = options.id ?? "tend:codex";
    this.mode = options.config.mode ?? "focused";
    if (!["focused", "trajectory"].includes(this.mode)) throw new Error(`Unknown eval mode: ${this.mode}`);
  }

  id() {
    return this.providerId;
  }

  async loadProvider(config) {
    const { loadApiProvider } = await import("promptfoo");
    return loadApiProvider("openai:codex-sdk", { options: { config } });
  }

  async codexPath() {
    // Use the pinned SDK's own platform resolver, including its native binary
    // layout. This internal field is checked against our pinned SDK version.
    const { Codex } = await import("@openai/codex-sdk");
    return fs.realpath(new Codex().exec.executablePath);
  }

  async callApi(prompt, context, callOptions) {
    if (process.env.OPENAI_API_KEY || process.env.CODEX_API_KEY) {
      return {
        error: "Codex evals require subscription auth. Unset OPENAI_API_KEY and CODEX_API_KEY before running them.",
      };
    }
    const sourceHome = process.env.CODEX_HOME ?? path.join(os.homedir(), ".codex");
    let auth;
    try {
      auth = await fs.readFile(path.join(sourceHome, "auth.json"));
      if (JSON.parse(auth).auth_mode !== "chatgpt") {
        return { error: "Codex evals require a ChatGPT subscription login (codex login)." };
      }
    } catch (error) {
      return { error: `Codex subscription auth is unavailable: ${error.message}` };
    }

    const root = await fs.mkdtemp(path.join(os.tmpdir(), "tend-codex-eval-"));
    const workspace = path.join(root, "workspace");
    const repository = path.join(workspace, "repository");
    const home = path.join(root, "home");
    const codexHome = path.join(home, ".codex");
    const observer = path.join(root, "observer.git");
    const git = (args) => execFileSync("git", args, {
      cwd: repository,
      encoding: "utf8",
      env: {
        PATH: process.env.PATH,
        HOME: home,
        GIT_CONFIG_NOSYSTEM: "1",
        GIT_CONFIG_GLOBAL: "/dev/null",
        GIT_DIR: observer,
        GIT_WORK_TREE: repository,
        GIT_OPTIONAL_LOCKS: "0",
        GIT_NO_REPLACE_OBJECTS: "1",
      },
    });
    let provider;
    let initialCommit;
    try {
      await fs.mkdir(codexHome, { recursive: true });
      await fs.writeFile(path.join(codexHome, "auth.json"), auth, { mode: 0o600 });
      const codexPath = await this.codexPath();
      // SDK config overrides flatten TOML keys, which cannot represent these
      // quoted path selectors. Keep the complete profile in its isolated home.
      await fs.writeFile(path.join(codexHome, "config.toml"), `default_permissions = "eval"
[permissions.eval.filesystem]
":root" = "deny"
":minimal" = "read"
${JSON.stringify(codexPath)} = "read"
":tmpdir" = "deny"
":slash_tmp" = "deny"
[permissions.eval.filesystem.":workspace_roots"]
"." = "${this.config.judge ? "read" : "write"}"
[permissions.eval.network]
enabled = false
`);
      const config = {
        model: this.config.model,
        codex_path_override: codexPath,
        model_reasoning_effort: "medium",
        working_dir: workspace,
        skip_git_repo_check: true,
        web_search_mode: "disabled",
        approval_policy: "never",
        persist_threads: false,
        inherit_process_env: false,
        cli_env: {
          HOME: home,
          CODEX_HOME: codexHome,
          CLAUDE_PLUGIN_ROOT: path.join(workspace, "plugin"),
        },
      };
      if (this.config.judge) {
        await fs.mkdir(workspace);
      } else {
        const arm = path.resolve(this.config.prepared);
        await fs.cp(path.join(arm, "workspace"), workspace, {
          recursive: true,
          verbatimSymlinks: true,
        });
        const originalWorkspace = path.join(arm, "workspace");
        const rebase = (text) => text.replaceAll(originalWorkspace, workspace);
        const instructions = rebase(await fs.readFile(path.join(workspace, "AGENTS.md"), "utf8"));
        await fs.writeFile(path.join(workspace, "AGENTS.md"), instructions);
        config.cli_config = { developer_instructions: instructions };
        if (this.mode === "trajectory") {
          if (!await exists(path.join(repository, ".git"))) throw new Error("Trajectory eval requires a Git checkout at repository/");
          // Observe object/ref data through a separate Git directory. Actor
          // config, hooks and index never enter the unsandboxed observer.
          await fs.mkdir(observer);
          for (const name of ["objects", "refs"]) {
            await fs.symlink(path.join(repository, ".git", name), path.join(observer, name));
          }
          await copyHead(repository, observer);
          initialCommit = git(["rev-parse", "HEAD"]).trim();
        }
      }
      provider = await this.loadProvider(config);
      const response = await provider.callApi(prompt, context, callOptions);
      if (response.error || this.config.judge) return response;
      let output;
      try {
        output = await fs.readFile(path.join(workspace, "captured.md"), "utf8");
      } catch (error) {
        if (error.code !== "ENOENT") throw error;
        return { ...response, output: undefined, error: "No captured.md draft was written by Codex" };
      }
      if (this.mode === "trajectory") {
        await copyHead(repository, observer);
        const finalHead = git(["rev-parse", "HEAD"]).trim();
        git(["read-tree", finalHead]);
        output = JSON.stringify({
          artifact: output,
          items: JSON.parse(response.raw).items,
          ...(initialCommit ? {
            gitDiff: git(["diff", initialCommit]),
            gitStatus: git(["status", "--porcelain"]),
            initialHead: initialCommit,
            finalHead,
          } : {}),
        }, null, 2);
      }
      return {
        ...response,
        output,
        metadata: { ...response.metadata, capturedFile: "captured.md" },
      };
    } catch (error) {
      return { error: `Codex eval execution failed: ${error.message}` };
    } finally {
      try {
        if (provider) await provider.shutdown();
      } finally {
        await fs.rm(root, { recursive: true, force: true });
      }
    }
  }
};

async function copyHead(repository, observer) {
  await fs.copyFile(path.join(repository, ".git", "HEAD"), path.join(observer, "HEAD"));
  const packed = path.join(repository, ".git", "packed-refs");
  const target = path.join(observer, "packed-refs");
  if (await exists(packed)) await fs.copyFile(packed, target);
  else await fs.rm(target, { force: true });
}

async function exists(file) {
  try {
    await fs.access(file);
    return true;
  } catch (error) {
    if (error.code === "ENOENT") return false;
    throw error;
  }
}
