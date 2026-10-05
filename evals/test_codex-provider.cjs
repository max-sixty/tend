const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { test } = require("node:test");
const CodexProvider = require("./codex-provider.cjs");
const repositoryUnchanged = require("./repository-unchanged.cjs");
const reviewEvidence = require("./review-evidence.cjs");

test("review evidence rejects blank artifacts and claims without recorded commands", () => {
  const command = { type: "command_execution", command: "cat source.txt" };
  const inspected = { artifact: "Review decision", items: [command] };
  for (const output of [
    "not JSON", "null", "[]", "{}",
    JSON.stringify({ ...inspected, artifact: "" }),
    JSON.stringify({ ...inspected, artifact: " \n\t" }),
    JSON.stringify({ ...inspected, artifact: 1 }),
    JSON.stringify({ ...inspected, artifact: undefined }),
    JSON.stringify({ ...inspected, items: [] }),
    JSON.stringify({ ...inspected, items: null }),
    JSON.stringify({ ...inspected, items: command }),
    JSON.stringify({ ...inspected, items: [{ type: "agent_message", text: "I inspected the code" }] }),
    JSON.stringify({ ...inspected, items: [null, 1, { type: "command_execution" }] }),
    JSON.stringify({ ...inspected, items: [{ ...command, command: " \n" }] }),
  ]) assert.equal(reviewEvidence(output).pass, false, output);
  // A failed command is still real evidence; its relevance and limits belong
  // to the model judge, so this minimum-presence check must accept it.
  assert.equal(reviewEvidence(JSON.stringify({ ...inspected, items: [{ ...command, exit_code: 1 }] })).pass, true);
});

test("repository observations reject malformed or independently changed state", () => {
  const clean = { gitDiff: "", gitStatus: "", initialHead: "a".repeat(40), finalHead: "a".repeat(40) };
  for (const output of [
    "not JSON", "null", "[]", "{}",
    JSON.stringify({ ...clean, gitDiff: null }),
    JSON.stringify({ ...clean, gitStatus: undefined }),
    JSON.stringify({ ...clean, initialHead: "", finalHead: "" }),
    JSON.stringify({ ...clean, initialHead: " ", finalHead: " " }),
    JSON.stringify({ ...clean, initialHead: 1, finalHead: 1 }),
    JSON.stringify({ ...clean, gitDiff: "tracked modification" }),
    JSON.stringify({ ...clean, gitStatus: "?? untracked.txt\n" }),
    JSON.stringify({ ...clean, finalHead: "b".repeat(40) }),
  ]) assert.equal(repositoryUnchanged(output).pass, false, output);
});

test("fresh Codex tasks isolate evidence and expose artifacts or actual trajectories", async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "tend-provider-test-"));
  const authHome = path.join(root, "auth");
  await fs.mkdir(authHome);
  const auth = JSON.stringify({ auth_mode: "chatgpt", tokens: { access_token: "test" } });
  await fs.writeFile(path.join(authHome, "auth.json"), auth);
  const savedEnv = Object.fromEntries(["CODEX_HOME", "OPENAI_API_KEY", "CODEX_API_KEY"].map((key) => [key, process.env[key]]));
  process.env.CODEX_HOME = authHome;
  delete process.env.OPENAI_API_KEY;
  delete process.env.CODEX_API_KEY;
  t.after(async () => {
    for (const [key, value] of Object.entries(savedEnv)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    await fs.rm(root, { recursive: true, force: true });
  });
  // One prepared arm serves every case; the test's metadata selects one.
  const arm = path.join(root, "arm");
  const staged = path.join(arm, "case");
  const template = path.join(staged, "workspace");
  const run = (kind, name = "case") => ({ test: { metadata: { case: name, kind } } });
  await fs.mkdir(path.join(template, "plugin", "skills", "draft"), { recursive: true });
  await fs.writeFile(path.join(template, "plugin", "skills", "draft", "SKILL.md"), "Draft skill");
  await fs.mkdir(path.join(template, ".agents", "skills"), { recursive: true });
  await fs.symlink("../../plugin/skills/draft", path.join(template, ".agents", "skills", "draft"));
  await fs.writeFile(path.join(template, "AGENTS.md"), "Staged guidance");
  await fs.writeFile(path.join(template, "evidence.txt"), "Verified evidence");
  const repository = path.join(template, "repository");
  await fs.mkdir(repository);
  await fs.writeFile(path.join(repository, "source.txt"), "old\n");
  await fs.writeFile(path.join(repository, "dirty.txt"), "old\n");
  const git = (args) => execFileSync("git", args, {
    cwd: repository, encoding: "utf8", env: { PATH: process.env.PATH, HOME: root, GIT_CONFIG_NOSYSTEM: "1" },
  });
  git(["init", "-q"]);
  git(["add", "."]);
  git(["-c", "user.name=Eval", "-c", "user.email=eval@example.com", "commit", "-qm", "Case starting tree"]);
  const transcript = "Original transcripts are not actor inputs";
  await fs.writeFile(path.join(staged, "history.jsonl"), transcript);
  const usage = { prompt: 70, cached: 60, completion: 3, total: 73 };
  const items = [{ type: "command_execution", command: "run real tests", aggregated_output: "tests passed", exit_code: 0 }];
  const raw = JSON.stringify({ items, finalResponse: "Final response differs from file" });
  const workspaces = [];
  const helperMarker = path.join(root, "observer-helper-ran");
  const helper = path.join(root, "observer-helper.sh");
  await fs.writeFile(helper, `#!/bin/sh\nprintf executed > '${helperMarker.replaceAll("'", "'\\''")}'\ncat >/dev/null\nprintf 'old\\n'\n`, { mode: 0o755 });
  // Replace only the external model call. The real adapter creates, stages,
  // reads and retires files; no model login or nondeterministic output is needed.
  class DeterministicProvider extends CodexProvider {
    async codexPath() { return "/usr/bin/true"; }
    async loadProvider(config) {
      workspaces.push(config.working_dir);
      assert.equal(config.thread_id, undefined);
      await assert.rejects(fs.access(path.join(config.cli_env.CODEX_HOME, "sessions")), { code: "ENOENT" });
      await assert.rejects(fs.access(path.join(config.working_dir, "history.jsonl")), { code: "ENOENT" });
      assert.equal(await fs.readFile(path.join(config.working_dir, ".agents", "skills", "draft", "SKILL.md"), "utf8"), "Draft skill");
      assert.equal(await fs.readlink(path.join(config.working_dir, ".agents", "skills", "draft")), "../../plugin/skills/draft");
      assert.equal(config.cli_config.developer_instructions, "Staged guidance");
      return {
        callApi: async (prompt) => {
          assert.ok(["A", "B", "Trajectory brief", "Read-only brief"].includes(prompt));
          assert.equal(await fs.readFile(path.join(config.working_dir, "evidence.txt"), "utf8"), "Verified evidence");
          if (prompt === "Read-only brief") {
            await fs.writeFile(path.join(config.working_dir, "captured.md"), "Read-only review\n");
            return { output: "Final response differs from file", tokenUsage: usage, raw };
          }
          await fs.writeFile(path.join(config.working_dir, "evidence.txt"), "changed by this attempt");
          await fs.writeFile(path.join(config.working_dir, "repository", "source.txt"), "new\n");
          await fs.writeFile(path.join(config.working_dir, "repository", "untracked.txt"), "new file\n");
          const actorGit = (args) => execFileSync("git", args, {
            cwd: path.join(config.working_dir, "repository"), encoding: "utf8",
            env: { PATH: process.env.PATH, HOME: config.cli_env.HOME, GIT_CONFIG_NOSYSTEM: "1" },
          });
          actorGit(["add", "source.txt"]);
          actorGit(["-c", "user.name=Eval", "-c", "user.email=eval@example.com", "commit", "-qm", "Actor change"]);
          actorGit(["config", "diff.external", helper]);
          actorGit(["config", "core.fsmonitor", helper]);
          actorGit(["config", "core.hooksPath", path.dirname(helper)]);
          actorGit(["config", "diff.hidden.textconv", helper]);
          actorGit(["config", "filter.hide.clean", helper]);
          await fs.writeFile(path.join(config.working_dir, "repository", "dirty.txt"), "dirty\n");
          await fs.writeFile(path.join(config.working_dir, "repository", ".gitattributes"), "source.txt diff=hidden\ndirty.txt filter=hide\n");
          const fakeTree = path.join(config.working_dir, "fake-tree");
          await fs.mkdir(fakeTree);
          await fs.writeFile(path.join(fakeTree, "source.txt"), "old\n");
          actorGit(["config", "core.worktree", fakeTree]);
          await fs.writeFile(path.join(config.working_dir, "captured.md"), `Literal ${prompt}\n\n`);
          return { output: "Final response differs from file", tokenUsage: usage, raw };
        },
        shutdown: async () => {},
      };
    }
  }
  const provider = new DeterministicProvider({ config: { prepared: arm, model: "gpt-6.1-sol" } });
  const results = await Promise.all([provider.callApi("A", run("focused")), provider.callApi("B", run("focused"))]);
  assert.deepEqual(results.map((result) => result.output), ["Literal A\n\n", "Literal B\n\n"]);
  assert.deepEqual(results.map((result) => [result.tokenUsage, result.raw]), [[usage, raw], [usage, raw]]);
  const longer = await provider.callApi("Trajectory brief", run("trajectory"));
  const trajectory = JSON.parse(longer.output);
  assert.equal(trajectory.artifact, "Literal Trajectory brief\n\n");
  assert.deepEqual(trajectory.items, items);
  assert.match(trajectory.gitDiff, /\n-old\n\+new\n/);
  assert.match(trajectory.gitDiff, /\n-old\n\+dirty\n/);
  assert.match(trajectory.gitStatus, / M dirty\.txt/);
  assert.match(trajectory.gitStatus, /\?\? untracked\.txt/);
  assert.equal(trajectory.initialHead, git(["rev-parse", "HEAD"]).trim());
  assert.notEqual(trajectory.finalHead, trajectory.initialHead);
  assert.equal(repositoryUnchanged(longer.output).pass, false);
  assert.equal(reviewEvidence(longer.output).pass, true);
  const unchanged = await provider.callApi("Read-only brief", run("trajectory"));
  assert.equal(unchanged.error, undefined);
  assert.equal(repositoryUnchanged(unchanged.output).pass, true);
  assert.equal(reviewEvidence(unchanged.output).pass, true);
  await assert.rejects(fs.access(helperMarker), { code: "ENOENT" });
  assert.equal(longer.raw, raw);
  assert.equal(new Set(workspaces).size, 4);
  for (const workspace of workspaces) await assert.rejects(fs.access(workspace), { code: "ENOENT" });
  assert.equal(await fs.readFile(path.join(staged, "history.jsonl"), "utf8"), transcript);
  assert.equal(await fs.readFile(path.join(template, "evidence.txt"), "utf8"), "Verified evidence");
  assert.equal(await fs.readFile(path.join(repository, "source.txt"), "utf8"), "old\n");
  assert.equal(await fs.readFile(path.join(authHome, "auth.json"), "utf8"), auth);

  class EmptyProvider extends CodexProvider {
    async codexPath() { return "/usr/bin/true"; }
    async loadProvider(config) {
      if (this.config.judge) {
        assert.deepEqual(await fs.readdir(config.working_dir), []);
        assert.equal(config.thread_id, undefined);
      }
      return {
        callApi: async () => {
          return { output: '{"pass":true}', tokenUsage: usage };
        },
        shutdown: async () => {},
      };
    }
  }
  const missing = await new EmptyProvider({ config: { prepared: arm } }).callApi("draft", run("focused"));
  assert.equal(missing.error, "No captured.md draft was written by Codex");
  assert.deepEqual(missing.tokenUsage, usage);
  const judged = await new EmptyProvider({ config: { judge: true } }).callApi("grade");
  assert.equal(judged.output, '{"pass":true}');
  assert.equal(judged.error, undefined);
  const noCheckout = path.join(root, "no-checkout");
  await fs.mkdir(path.join(noCheckout, "workspace"), { recursive: true });
  await fs.writeFile(path.join(noCheckout, "workspace", "AGENTS.md"), "Staged guidance");
  const invalid = await new EmptyProvider({ config: { prepared: root } }).callApi("work", run("trajectory", "no-checkout"));
  assert.match(invalid.error, /Trajectory eval requires a Git checkout at repository\//);
  const unknown = await new EmptyProvider({ config: { prepared: arm } }).callApi("work", run("history"));
  assert.match(unknown.error, /Unknown eval kind: history/);

  process.env.OPENAI_API_KEY = "test-key";
  const rejected = await provider.callApi("draft");
  assert.match(rejected.error, /subscription auth/);
});
