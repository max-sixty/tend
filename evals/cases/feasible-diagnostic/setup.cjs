// Expose the existing Node executable to isolated attempts; no packages or network.
const fs = require('node:fs/promises');
const path = require('node:path');
module.exports = async function configure(workspace) {
  const node = await fs.realpath(process.execPath);
  const readPaths = [node];
  if (node.includes('/Cellar/')) {
    const prefix = node.split('/Cellar/')[0];
    readPaths.push(path.join(prefix, 'Cellar'), path.join(prefix, 'opt'));
  }
  const bin = path.join(workspace, '.runtime', 'bin');
  await fs.mkdir(bin, { recursive: true });
  await fs.symlink(node, path.join(bin, 'node'));
  return { readPaths, env: { PATH: `${bin}:/usr/bin:/bin`, OPENSSL_CONF: "/dev/null" } };
};
