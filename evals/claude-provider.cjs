/** Run a focused case through Promptfoo's Claude Agent SDK provider.
 * Each provider is one arm, a column of results: `config.prepared` holds that
 * arm's staged cases, and the test's metadata names the case. The case's plugin,
 * guidance and read-only inputs complete the SDK config for this call only. The
 * SDK provider gives every call a fresh temporary working directory, where the
 * agent writes captured.md.
 */
const fs = require("node:fs/promises");
const path = require("node:path");

module.exports = class ClaudeProvider {
  constructor(options) {
    const { prepared, ...settings } = options.config;
    this.prepared = prepared;
    this.settings = settings;
    this.providerId = options.id ?? "tend:claude";
  }

  id() {
    return this.providerId;
  }

  async loadProvider(config) {
    const { loadApiProvider } = await import("promptfoo");
    return loadApiProvider("anthropic:claude-agent-sdk", { options: { config } });
  }

  async callApi(prompt, context, callOptions) {
    const workspace = path.join(path.resolve(this.prepared), context.test.metadata.case, "workspace");
    const policy = await fs.readFile(path.join(workspace, "AGENTS.md"), "utf8");
    const provider = await this.loadProvider({
      ...this.settings,
      additional_directories: [workspace],
      plugins: [{ type: "local", path: path.join(workspace, "plugin") }],
      append_system_prompt: `${policy}\nRead the starting files from ${workspace}. Resolve relative evidence paths in the task under that directory. Write captured.md in your temporary working directory; the prepared input directory is read-only.\n`,
    });
    return provider.callApi(prompt, context, callOptions);
  }
};
