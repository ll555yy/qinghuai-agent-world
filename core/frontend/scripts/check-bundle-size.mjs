import { readdir, stat } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const defaultAssetsDirectory = path.join(frontendRoot, 'dist', 'assets')

const BYTES_PER_KILOBYTE = 1000
const budgets = {
  application: 500,
  relationshipGraphPanel: 250,
  phaserRuntime: 1200,
}

const requiredChunks = {
  relationshipGraphPanel: /^RelationshipGraphPanel(?:-[^/]+)?\.js$/i,
  phaserRuntime: /^phaser-runtime(?:-[^/]+)?\.js$/i,
}

function formatSize(bytes) {
  return `${(bytes / BYTES_PER_KILOBYTE).toFixed(1)} kB`
}

function usage() {
  return 'Usage: node scripts/check-bundle-size.mjs [assets-directory]'
}

function resolveAssetsDirectory() {
  const [candidate] = process.argv.slice(2)
  if (!candidate) return defaultAssetsDirectory
  if (candidate === '--help' || candidate === '-h') {
    console.log(usage())
    process.exit(0)
  }
  if (candidate.startsWith('-')) {
    throw new Error(`Unknown option: ${candidate}\n${usage()}`)
  }
  return path.resolve(process.cwd(), candidate)
}

function chunkKind(name) {
  if (requiredChunks.relationshipGraphPanel.test(name)) return 'relationshipGraphPanel'
  if (requiredChunks.phaserRuntime.test(name)) return 'phaserRuntime'
  return 'application'
}

async function readJavaScriptChunks(assetsDirectory) {
  let entries
  try {
    entries = await readdir(assetsDirectory, { withFileTypes: true })
  } catch (error) {
    if (error?.code === 'ENOENT') {
      throw new Error(`Build assets directory not found: ${assetsDirectory}. Run vite build first.`)
    }
    throw error
  }

  const chunks = []
  for (const entry of entries) {
    if (!entry.isFile() || !entry.name.endsWith('.js')) continue
    const filePath = path.join(assetsDirectory, entry.name)
    const fileStats = await stat(filePath)
    chunks.push({ name: entry.name, bytes: fileStats.size, kind: chunkKind(entry.name) })
  }

  return chunks.sort((left, right) => left.name.localeCompare(right.name))
}

function checkChunks(chunks) {
  const failures = []
  for (const [kind, pattern] of Object.entries(requiredChunks)) {
    const matches = chunks.filter((chunk) => pattern.test(chunk.name))
    if (matches.length === 0) {
      failures.push(`missing required ${kind === 'relationshipGraphPanel' ? 'RelationshipGraphPanel' : 'phaser-runtime'} chunk`)
    }
    if (matches.length > 1) {
      failures.push(
        `expected one ${kind === 'relationshipGraphPanel' ? 'RelationshipGraphPanel' : 'phaser-runtime'} chunk, found ${matches.length}`,
      )
    }
  }

  for (const chunk of chunks) {
    const budget = budgets[chunk.kind]
    const sizeInKilobytes = chunk.bytes / BYTES_PER_KILOBYTE
    if (sizeInKilobytes > budget) {
      failures.push(
        `${chunk.name} is ${formatSize(chunk.bytes)}, over the ${budget} kB ${
          chunk.kind === 'application'
            ? 'application'
            : chunk.kind === 'relationshipGraphPanel'
              ? 'RelationshipGraphPanel'
              : 'phaser-runtime'
        } budget`,
      )
    }
  }

  return failures
}

try {
  const assetsDirectory = resolveAssetsDirectory()
  const chunks = await readJavaScriptChunks(assetsDirectory)
  const failures = checkChunks(chunks)

  console.log(`Bundle size gate: ${assetsDirectory}`)
  if (chunks.length === 0) {
    console.error(`  FAIL: no JavaScript chunks found in ${assetsDirectory}.`)
    process.exitCode = 1
  } else {
    for (const chunk of chunks) {
      const budget = budgets[chunk.kind]
      console.log(`  ${chunk.name}: ${formatSize(chunk.bytes)} / ${budget} kB`)
    }
  }

  if (failures.length > 0) {
    console.error('Bundle size gate failed:')
    for (const failure of failures) console.error(`  - ${failure}`)
    process.exitCode = 1
  } else if (chunks.length > 0) {
    console.log('Bundle size gate passed.')
  }
} catch (error) {
  console.error(`Bundle size gate failed: ${error instanceof Error ? error.message : error}`)
  process.exitCode = 1
}
