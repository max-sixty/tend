const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");
const ClaudeProvider = require("./claude-provider.cjs");

test("one arm's provider gives each case its own plugin, guidance and inputs", async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "tend-claude-provider-test-"));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  for (const name of ["first", "second"]) {
    await fs.mkdir(path.join(root, name, "workspace"), { recursive: true });
    await fs.writeFile(path.join(root, name, "workspace", "AGENTS.md"), `Guidance for ${name}`);
  }
  const loaded = [];
  // Replace only the SDK provider; the response passes through unchanged.
  class RecordingProvider extends ClaudeProvider {
    async loadProvider(config) {
      loaded.push(config);
      return { callApi: async (prompt) => ({ output: `Ran ${prompt}` }) };
    }
  }
  const provider = new RecordingProvider({ config: { prepared: root, model: "claude-opus-5-5", max_turns: 32 } });
  const results = await Promise.all(["first", "second"].map((name) =>
    provider.callApi(name, { test: { metadata: { case: name, kind: "focused" } } })));
  assert.deepEqual(results.map((result) => result.output), ["Ran first", "Ran second"]);
  for (const name of ["first", "second"]) {
    const workspace = path.join(root, name, "workspace");
    const config = loaded.find((settings) => settings.additional_directories[0] === workspace);
    assert.equal(config.prepared, undefined);
    assert.equal(config.model, "claude-opus-5-5");
    assert.equal(config.max_turns, 32);
    assert.equal(config.working_dir, undefined);
    assert.deepEqual(config.additional_directories, [workspace]);
    assert.deepEqual(config.plugins, [{ type: "local", path: path.join(workspace, "plugin") }]);
    assert.ok(config.append_system_prompt.startsWith(`Guidance for ${name}\n`));
    assert.ok(config.append_system_prompt.includes(`Read the starting files from ${workspace}.`));
  }
});
