import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const frontend = new URL('../', import.meta.url)
const lockfile = JSON.parse(await readFile(new URL('package-lock.json', frontend), 'utf8'))

// Patched floors for the Dependabot advisories in this dependency tree.
// The plugin floor is the first 5.x release that supports Vite 6.
const minimumVersions = {
  vite: '6.4.4',
  '@vitejs/plugin-vue': '5.2.1',
  esbuild: '0.25.0',
  rollup: '4.59.0',
  picomatch: '4.0.4',
  postcss: '8.5.23',
  nanoid: '3.3.18',
  vue: '3.5.42',
  '@vue/server-renderer': '3.5.42',
  'source-map-js': '1.2.2',
}

function assertPatchedVersion(version, minimum, label) {
  assert.match(version, /^\d+\.\d+\.\d+$/, `${label} must be a stable release`)
  const actual = version.split('.').map(Number)
  const required = minimum.split('.').map(Number)
  const difference = actual.map((part, index) => part - required[index]).find((part) => part !== 0) ?? 0
  assert.ok(difference >= 0, `${label}: ${version} is below patched floor ${minimum}`)
}

for (const [name, minimum] of Object.entries(minimumVersions)) {
  test(`${name} is patched in every locked and installed copy`, async () => {
    const copies = Object.entries(lockfile.packages).filter(([path]) => (
      path === `node_modules/${name}` || path.endsWith(`/node_modules/${name}`)
    ))
    assert.ok(copies.length > 0, `${name} must be present in the dependency tree`)
    for (const [path, entry] of copies) {
      assertPatchedVersion(entry.version, minimum, path)
      const installed = JSON.parse(await readFile(new URL(`${path}/package.json`, frontend), 'utf8'))
      assert.equal(installed.version, entry.version, `${path} must match the lockfile`)
      assertPatchedVersion(installed.version, minimum, `installed ${path}`)
    }
  })
}
