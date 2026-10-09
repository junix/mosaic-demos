import {spawn} from 'node:child_process';
import {mkdir, readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {PNG} from 'pngjs';
import {chromium} from 'playwright-core';

const root = resolve(import.meta.dirname, '..');
const renderOnly = process.argv.includes('--render-only');
const port = 41732;
const chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const vite = resolve(root, 'node_modules/vite/bin/vite.js');
const server = spawn(process.execPath, [vite, 'preview', '--host', '127.0.0.1', '--port', String(port)], {cwd: root, stdio: 'pipe'});
const failures = [];
const catalog = JSON.parse(await readFile(resolve(root, 'catalog.json'), 'utf8'));
if (catalog.length < 12) throw new Error('reference library requires at least 12 scenes');

async function waitForServer() {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try { if ((await fetch(`http://127.0.0.1:${port}`)).ok) return; } catch {}
    await new Promise(resolveWait => setTimeout(resolveWait, 120));
  }
  throw new Error('Vite preview did not become ready');
}

function inspectTransparentPng(buffer, scene) {
  const image = PNG.sync.read(buffer);
  let clear = 0;
  let ink = 0;
  let saturated = 0;
  for (let index = 0; index < image.data.length; index += 4) {
    const r = image.data[index];
    const g = image.data[index + 1];
    const b = image.data[index + 2];
    const alpha = image.data[index + 3];
    if (alpha < 8) clear += 1;
    if (alpha > 32) ink += 1;
    if (alpha > 80 && Math.max(r, g, b) - Math.min(r, g, b) > 28) saturated += 1;
  }
  const pixels = image.width * image.height;
  if (clear < pixels * 0.18) failures.push(`${scene}: transparent area too small (${clear}/${pixels})`);
  if (ink < 8_000) failures.push(`${scene}: visible content too small (${ink} pixels)`);
  if (saturated < 1_200) failures.push(`${scene}: colored data marks missing (${saturated} pixels)`);
  return {width: image.width, height: image.height, clear, ink};
}

// The rankings scene must answer its catalog question ("How do regional scores
// and uncertainty compare?"). The scene exposes its DuckDB aggregates as
// window.__mosaicDemo.rankings; recompute mean and 10th–90th percentile per
// region from the deterministic value generator in src/main.ts (kept in sync
// here on purpose) and require matching endpoints, mean-inside-interval
// pairing, mean-ordered ranks, and horizontal interval rules wider than dots.
function verifyRankingsInterval(state, failures) {
  const rows = state.rankings ?? [];
  if (rows.length !== 8) {
    failures.push(`rankings: expected 8 aggregated regions, got ${rows.length}`);
    return;
  }
  const scores = new Map();
  for (let cohort = 0; cohort < 8; cohort += 1) scores.set(cohort, []);
  for (let i = 0; i < state.rows; i += 1) {
    const value = 42 + 18 * Math.sin(i * 0.017) + 11 * Math.cos(i * 0.0043) + (i % 31) / 3.0;
    scores.get(Math.floor(i / 96) % 8).push(value);
  }
  const quantile = (sorted, p) => {
    const h = (sorted.length - 1) * p;
    const lo = Math.floor(h);
    return sorted[lo] + (h - lo) * (sorted[Math.min(lo + 1, sorted.length - 1)] - sorted[lo]);
  };
  const byRank = [...rows].sort((a, b) => a.rank - b.rank);
  for (let k = 1; k < byRank.length; k += 1) {
    if (byRank[k].mean > byRank[k - 1].mean) failures.push('rankings: ranks not ordered by displayed mean');
  }
  for (const row of rows) {
    const sorted = scores.get(row.cohort).sort((a, b) => a - b);
    const mean = sorted.reduce((sum, value) => sum + value, 0) / sorted.length;
    const checks = [
      ['sample count', row.n, sorted.length, 0],
      ['mean', row.mean, mean, 1e-8],
      ['10th percentile', row.q10, quantile(sorted, 0.1), 1e-8],
      ['90th percentile', row.q90, quantile(sorted, 0.9), 1e-8]
    ];
    for (const [name, got, want, tolerance] of checks) {
      if (Math.abs(got - want) > tolerance) failures.push(`rankings: region ${row.cohort} ${name} is ${got}, expected ${want}`);
    }
    if (!(row.q10 <= row.mean && row.mean <= row.q90)) failures.push(`rankings: region ${row.cohort} mean outside its interval`);
  }
}

try {
  await mkdir(resolve(root, 'out'), {recursive: true});
  await waitForServer();
  const browser = await chromium.launch({headless: true, executablePath: chrome});
  for (const {id: scene} of catalog) {
    const page = await browser.newPage({viewport: {width: 1400, height: 900}, deviceScaleFactor: 1});
    const errors = [];
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.hostname === '127.0.0.1' || ['blob:', 'data:'].includes(url.protocol)) route.continue();
      else { failures.push(`${scene}: blocked external request ${url.href}`); route.abort(); }
    });
    await page.goto(`http://127.0.0.1:${port}/?scene=${scene}&export=1`, {waitUntil: 'networkidle'});
    await page.waitForFunction(() => window.__mosaicDemo?.ready === true || Boolean(window.__mosaicDemo?.error), undefined, {timeout: 40_000});
    const state = await page.evaluate(() => ({...window.__mosaicDemo}));
    if (state.error) throw new Error(`${scene}: ${state.error}`);
    if (scene === 'rankings') {
      verifyRankingsInterval(state, failures);
      const wideRules = await page.$$eval('#chart svg line', lines => lines
        .filter(line => Math.abs(Number(line.getAttribute('x2')) - Number(line.getAttribute('x1'))) > 60)
        .length);
      if (wideRules < 8) failures.push(`rankings: interval bars missing (${wideRules} rules wider than a dot)`);
    }
    await page.waitForSelector('#chart svg, #chart canvas', {timeout: 10_000});
    await page.waitForTimeout(450);
    const path = resolve(root, 'out', `${scene}-transparent.png`);
    await page.screenshot({path, omitBackground: true});
    const stats = inspectTransparentPng(await readFile(path), scene);
    if (!renderOnly) {
      const plot = page.locator('#chart svg, #chart canvas').first();
      const box = await plot.boundingBox();
      if (!box) throw new Error(`${scene}: plot has no bounds`);
      await page.mouse.move(box.x + box.width * 0.35, box.y + box.height * 0.42);
      await page.mouse.down();
      await page.mouse.move(box.x + box.width * 0.63, box.y + box.height * 0.64, {steps: 8});
      await page.mouse.up();
      await page.waitForFunction(() => (window.__mosaicDemo?.interactions ?? 0) > 0);
      // Generic pointer events alone prove nothing about the crossfilter: the
      // linked and operations scenes expose window.__mosaicDemo.selection as a
      // re-queried count of the rows their dependent views currently match.
      // Require the deterministic brush above to shrink that count below the
      // full row count, and a tap on the overlay (which clears the d3 brush)
      // to restore it. Other scenes keep the pointer smoke check only.
      if (scene === 'linked' || scene === 'operations') {
        const readProbe = () => page.evaluate(() => ({...window.__mosaicDemo?.selection}));
        const baseline = state.selection;
        if (!baseline || baseline.count !== state.rows) {
          failures.push(`${scene}: selection probe must start at all ${state.rows} rows, got ${baseline ? baseline.count : 'no probe'}`);
        }
        await page.waitForFunction(
          rows => {
            const {selection} = window.__mosaicDemo ?? {};
            return Boolean(selection && !selection.error && selection.count > 0 && selection.count < rows);
          },
          state.rows,
          {timeout: 20_000}
        ).catch(async () => failures.push(`${scene}: deterministic brush did not change the linked query (probe: ${JSON.stringify(await readProbe())})`));
        const brushed = await page.evaluate(() => window.__mosaicDemo?.selection?.count);
        await page.mouse.move(box.x + box.width * 0.18, box.y + box.height * 0.28);
        await page.mouse.down();
        await page.mouse.up();
        await page.waitForFunction(
          rows => {
            const {selection} = window.__mosaicDemo ?? {};
            return Boolean(selection && !selection.error && selection.count === rows);
          },
          state.rows,
          {timeout: 20_000}
        ).catch(async () => failures.push(`${scene}: brush reset did not restore the linked query (probe: ${JSON.stringify(await readProbe())})`));
        console.log(`linked ${scene}: brush matched ${brushed}/${state.rows} rows, reset restored all`);
      }
    }
    if (errors.length) failures.push(`${scene}: console errors: ${errors.join(' | ')}`);
    console.log(`rendered ${scene}: ${state.rows} DB rows -> ${stats.width}x${stats.height} transparent PNG`);
    await page.close();
  }
  await browser.close();
} finally {
  server.kill('SIGTERM');
}

if (failures.length) {
  console.error(failures.join('\n'));
  process.exit(1);
}
console.log(`Mosaic ${renderOnly ? 'render' : 'validation'} passed: ${catalog.length} transparent PNGs`);
