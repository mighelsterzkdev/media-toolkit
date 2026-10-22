import { createHash } from 'node:crypto'
import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const manifestName = 'BUILD-MANIFEST.json'
const textExtensions = new Set(['.css', '.html', '.js', '.json', '.mjs', '.ts', '.tsx', '.txt', '.yaml', '.yml'])

async function filesUnder(directory) {
  const metadata = await fs.lstat(directory)
  if (!metadata.isDirectory() || metadata.isSymbolicLink()) throw new Error(`Build inputs must be regular directories: ${directory}`)
  const result = []
  for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
    const file = path.join(directory, entry.name)
    if (entry.isSymbolicLink()) throw new Error(`Build inputs must be regular files: ${file}`)
    if (entry.isDirectory()) result.push(...await filesUnder(file))
    else if (entry.isFile()) result.push(file)
    else throw new Error(`Unsupported build input: ${file}`)
  }
  return result
}

function inputPath(root, relative) {
  if (typeof relative !== 'string' || path.isAbsolute(relative) || relative.split(/[\\/]/).includes('..')) throw new Error('Invalid frontend build input path')
  return path.join(root, relative)
}

async function inventory(root, files) {
  const result = {}
  for (const file of files.sort()) {
    if ((await fs.lstat(file)).isSymbolicLink()) throw new Error(`Build inputs must be regular files: ${file}`)
    let content = await fs.readFile(file)
    // Git stores these text files as LF on every platform. Newlines do not
    // change their meaning and must not make a Windows build look stale.
    if (textExtensions.has(path.extname(file))) content = Buffer.from(content.toString('utf8').replace(/\r\n/g, '\n'), 'utf8')
    result[path.relative(root, file).split(path.sep).join('/')] = createHash('sha256').update(content).digest('hex')
  }
  return result
}

export async function writeBuildManifest(uiRoot, target) {
  const definition = JSON.parse(await fs.readFile(path.join(uiRoot, 'build-inputs.json'), 'utf8'))
  if (Object.keys(definition).sort().join(',') !== 'directories,files'
    || !Array.isArray(definition.files) || !Array.isArray(definition.directories)) throw new Error('Invalid frontend build input definition')
  const sources = definition.files.map(relative => inputPath(uiRoot, relative))
  for (const relative of definition.directories) sources.push(...await filesUnder(inputPath(uiRoot, relative)))
  const outputs = (await filesUnder(target)).filter(file => path.relative(target, file) !== manifestName)
  const manifest = { version: 1, sources: await inventory(uiRoot, sources), outputs: await inventory(target, outputs) }
  await fs.writeFile(path.join(target, manifestName), JSON.stringify(manifest, null, 2) + '\n', 'utf8')
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (process.argv.length !== 4) throw new Error('Usage: node build-manifest.mjs <frontend directory> <built asset directory>')
  await writeBuildManifest(path.resolve(process.argv[2]), path.resolve(process.argv[3]))
}
