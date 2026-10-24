// Every place that carries the app version, kept in step by one command.
//   npm run version:set -- 0.10.1     update them all
//   npm run version:check             fail if they disagree (build-release.ps1 runs this)
//   node scripts/bump-version.mjs --dry-run 0.10.1
// scripts/native-smoke.mjs reads the version from tauri.conf.json at run time.
// The "최신 버전" sentence in README.md describes the release, so it stays manual.
import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';

const root = fileURLToPath(new URL('..', import.meta.url));
// [file, pattern with the version in group 2, expected matches]
const PLACES = [
  ['package.json', /^(\s*"version": ")([^"]+)(")/m, 1],
  ['package-lock.json', /("name": "paperduet",\r?\n\s*"version": ")([^"]+)(")/g, 2],
  ['src-tauri/Cargo.toml', /(\[package\][^[]*?\nversion\s*=\s*")([^"]+)(")/, 1],
  ['src-tauri/Cargo.lock', /(name = "paperduet"\r?\nversion = ")([^"]+)(")/g, 1],
  ['src-tauri/tauri.conf.json', /^(\s*"version": ")([^"]+)(")/m, 1],
  ['README.md', /(PaperDuet_)(\d+\.\d+\.\d+)(_x64-setup\.exe)/g, null],
];

function versionsIn(text, pattern) {
  const flags = pattern.flags.includes('g') ? pattern.flags : pattern.flags + 'g';
  return [...text.matchAll(new RegExp(pattern.source, flags))].map(m => m[2]);
}

async function read() {
  const found = [];
  for (const [file, pattern, expected] of PLACES) {
    const text = await readFile(join(root, file), 'utf8');
    const versions = versionsIn(text, pattern);
    if (!versions.length || (expected !== null && versions.length !== expected)) {
      throw new Error(`${file}: expected ${expected ?? 'some'} version string(s), found ${versions.length}`);
    }
    found.push({ file, text, pattern, versions });
  }
  return found;
}

const args = process.argv.slice(2);
const dryRun = args.includes('--dry-run');
const target = args.find(a => !a.startsWith('--'));
const places = await read();

if (args.includes('--check') || !target) {
  const all = places.flatMap(p => p.versions.map(v => [p.file, v]));
  const distinct = [...new Set(all.map(([, v]) => v))];
  for (const [file, v] of all) console.log(`${v.padEnd(10)} ${file}`);
  if (distinct.length !== 1) {
    console.error(`Version strings disagree (${distinct.join(', ')}). Run: npm run version:set -- <version>`);
    process.exit(1);
  }
  console.log(`All version strings are ${distinct[0]}.`);
  if (!target) process.exit(0);
}

if (!/^\d+\.\d+\.\d+$/.test(target)) {
  console.error(`Not a version: ${target} (expected X.Y.Z)`);
  process.exit(1);
}
for (const p of places) {
  const next = p.text.replace(p.pattern, (_m, head, _old, tail) => head + target + tail);
  if (versionsIn(next, p.pattern).some(v => v !== target)) throw new Error(`${p.file}: not every version string was replaced`);
  // The JSON files must still parse and carry the new version where npm/Tauri read it.
  if (p.file.endsWith('.json')) {
    const data = JSON.parse(next);
    const read = p.file === 'package-lock.json' ? [data.version, data.packages[''].version] : [data.version];
    if (read.some(v => v !== target)) throw new Error(`${p.file}: the top-level version did not change`);
  }
  console.log(`${dryRun ? 'would set' : 'set'} ${p.file}: ${[...new Set(p.versions)].join(', ')} -> ${target}`);
  if (!dryRun && next !== p.text) await writeFile(join(root, p.file), next, 'utf8');
}
console.log('Remember the "최신 버전" sentence in README.md and the release notes.');
