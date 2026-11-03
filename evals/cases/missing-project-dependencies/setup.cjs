/** Supply executable Node tools and offline packages, leaving installation to the actor.
 * Preparation fetches the lockfile's packages in a disposable copy, retaining
 * only npm's content cache. Preparation needs npm and package-registry access.
 * Attempts use the host's architecture and Node/npm/Git; Python and browsers
 * are not provisioned. No preparation logs reach the actor.
 */
const fs = require("node:fs/promises");
const { existsSync } = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

async function prepare(workspace) {
  const scratch = await fs.mkdtemp(path.join(os.tmpdir(), "tend-eval-node-"));
  try {
    const checkout = path.join(scratch, "repository");
    const cache = path.join(scratch, "npm-cache");
    await fs.cp(path.join(workspace, "repository"), checkout, { recursive: true, verbatimSymlinks: true });
    execFileSync("npm", ["ci", "--ignore-scripts", "--offline=false", "--no-audit", "--no-fund", "--cache", cache], {
      cwd: checkout, stdio: "pipe",
    });
    const destination = path.join(workspace, ".npm");
    await fs.mkdir(destination);
    if (existsSync(path.join(cache, "_cacache"))) {
      await fs.cp(path.join(cache, "_cacache"), path.join(destination, "_cacache"), { recursive: true });
    }
  } finally {
    await fs.rm(scratch, { recursive: true, force: true });
  }
}

module.exports = async function configure(workspace) {
  const node = await fs.realpath(process.execPath);
  const npm = await fs.realpath(execFileSync("which", ["npm"], { encoding: "utf8" }).trim());
  const git = await fs.realpath(execFileSync("which", ["git"], { encoding: "utf8" }).trim());
  const readPaths = [node, path.dirname(path.dirname(npm)), path.dirname(path.dirname(git))];
  // Homebrew's loader traverses opt/ symlinks into Cellar/.
  for (const executable of [node, git]) {
    if (executable.includes("/Cellar/")) {
      const prefix = executable.split("/Cellar/")[0];
      readPaths.push(path.join(prefix, "Cellar"), path.join(prefix, "opt"));
    }
  }
  const bin = path.join(workspace, ".runtime", "bin");
  await fs.mkdir(bin, { recursive: true });
  for (const [name, executable] of Object.entries({ node, npm, git })) {
    await fs.symlink(executable, path.join(bin, name));
  }
  const tmp = path.join(workspace, ".runtime", "tmp");
  await fs.mkdir(tmp);
  const npmrc = path.join(workspace, ".runtime", "npmrc");
  await fs.writeFile(npmrc, "");
  return {
    readPaths: [...new Set(readPaths)],
    env: {
      PATH: `${bin}:/usr/bin:/bin`,
      TMPDIR: tmp,
      OPENSSL_CONF: "/dev/null",
      GIT_CONFIG_NOSYSTEM: "1",
      GIT_CONFIG_GLOBAL: "/dev/null",
      NPM_CONFIG_CACHE: path.join(workspace, ".npm"),
      NPM_CONFIG_OFFLINE: "true",
      NPM_CONFIG_USERCONFIG: npmrc,
      NPM_CONFIG_GLOBALCONFIG: "/dev/null",
      NPM_CONFIG_AUDIT: "false",
      NPM_CONFIG_FUND: "false",
    },
  };
};

if (require.main === module) prepare(process.argv[2]).catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
