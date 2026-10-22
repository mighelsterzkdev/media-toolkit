import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import fs from 'node:fs/promises'
import { writeBuildManifest } from './build-manifest.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const manifest = JSON.parse(await fs.readFile(path.join(root, 'package.json'), 'utf8'))
const packages = new Map()

async function packageDirectory(name, from) {
  const require = createRequire(path.join(from, 'package.json'))
  try { return path.dirname(require.resolve(`${name}/package.json`)) } catch { /* Some packages hide their manifest using exports. */ }
  let directory = path.dirname(require.resolve(name))
  while (true) {
    try {
      const metadata = JSON.parse(await fs.readFile(path.join(directory, 'package.json'), 'utf8'))
      if (metadata.name === name) return directory
    } catch { /* Search toward the package root. */ }
    const parent = path.dirname(directory)
    if (parent === directory) throw new Error(`Cannot locate license metadata for ${name}`)
    directory = parent
  }
}

async function collect(name, from) {
  const directory = await packageDirectory(name, from)
  const metadata = JSON.parse(await fs.readFile(path.join(directory, 'package.json'), 'utf8'))
  const key = `${metadata.name}@${metadata.version}`
  if (packages.has(key)) return
  packages.set(key, { directory, metadata })
  for (const dependency of Object.keys(metadata.dependencies ?? {})) await collect(dependency, directory)
}
for (const name of Object.keys(manifest.dependencies)) await collect(name, root)

let notices = 'Media Toolkit desktop interface — third-party notices\n\n'
notices += 'shadcn/ui components\nSource: https://ui.shadcn.com/r/styles/new-york/\n'
notices += 'Components: button, input, label, dialog, radio-group, progress, textarea, tabs, select\n'
notices += 'Source license: https://github.com/shadcn-ui/ui/blob/main/LICENSE.md\n\n'
notices += await fs.readFile(path.join(root, 'SHADCN-LICENSE.txt'), 'utf8')
for (const [name, { directory, metadata }] of [...packages].sort(([a], [b]) => a.localeCompare(b))) {
  const files = (await fs.readdir(directory)).filter(file => /^(licen[sc]e|copying|notice)(?:[.-].*)?$/i.test(file))
  notices += `\n\n${'='.repeat(72)}\n${name}\nDeclared license: ${metadata.license ?? 'See license text'}\n`
  if (metadata.homepage) notices += `Homepage: ${metadata.homepage}\n`
  if (files.length === 0) {
    const fallback = path.join(root, 'licenses', `${metadata.name.replace(/[@/]/g, '_')}.txt`)
    try { notices += `\nUpstream license (omitted from the npm package):\n${await fs.readFile(fallback, 'utf8')}\n` }
    catch { throw new Error(`Missing license text for runtime dependency ${name}`) }
  }
  for (const file of files.sort()) notices += `\n${file}\n${await fs.readFile(path.join(directory, file), 'utf8')}\n`
}

const target = path.resolve(root, '../src/universal_downloader/desktop_assets')
const expected = path.resolve(root, '..', 'src', 'universal_downloader', 'desktop_assets')
if (target !== expected || path.basename(target) !== 'desktop_assets') throw new Error('Invalid desktop build destination')
await fs.mkdir(path.join(target, 'assets'), { recursive: true })
// Only remove generated regular asset files; never traverse unrelated directories.
for (const entry of await fs.readdir(path.join(target, 'assets'), { withFileTypes: true })) {
  if (entry.isFile() && /\.(?:js|css)$/.test(entry.name)) await fs.unlink(path.join(target, 'assets', entry.name))
}
await fs.copyFile(path.join(root, 'dist', 'index.html'), path.join(target, 'index.html'))
for (const entry of await fs.readdir(path.join(root, 'dist', 'assets'), { withFileTypes: true })) {
  if (!entry.isFile()) throw new Error('Unexpected nested asset directory')
  await fs.copyFile(path.join(root, 'dist', 'assets', entry.name), path.join(target, 'assets', entry.name))
}
await fs.writeFile(path.join(target, 'THIRD-PARTY-NOTICES.txt'), notices, 'utf8')
await writeBuildManifest(root, target)
console.log(`Copied desktop assets and notices for ${packages.size} runtime dependencies.`)
