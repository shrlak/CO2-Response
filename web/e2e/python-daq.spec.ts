import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';

/**
 * The dashboard fed by the Python acquisition package (python/, `co2r-daq serve`)
 * running its simulated DAQ: the same path an NI or LabJack DAQ takes.
 * Skipped when Python with `websockets` is not available (CI installs it).
 */
const PY = process.env.CO2R_PYTHON ?? (process.platform === 'win32' ? 'python' : 'python3');
const PKG = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', 'python');
const PORT = 8799;
const env = { ...process.env, PYTHONPATH: PKG, PYTHONUNBUFFERED: '1' };
const hasPython = spawnSync(PY, ['-c', 'import websockets, co2r'], { env }).status === 0;

let server: ChildProcess | null = null;

test.beforeAll(async () => {
  if (!hasPython) return;
  server = spawn(PY, ['-m', 'co2r', 'serve', '--driver', 'simulated', '--seed', '1', '--ws-port', String(PORT)], { env, stdio: ['ignore', 'pipe', 'pipe'] });
  await new Promise<void>((ok, fail) => {
    const t = setTimeout(() => fail(new Error('co2r-daq did not start')), 15_000);
    server!.stdout!.on('data', (d: Buffer) => {
      if (String(d).includes('serving on')) {
        clearTimeout(t);
        ok();
      }
    });
    server!.on('exit', (code) => fail(new Error(`co2r-daq exited with ${code}`)));
  });
});

test.afterAll(() => {
  server?.kill();
});

test('dashboard runs on the Python acquisition server (simulated DAQ)', async ({ page }) => {
  test.skip(!hasPython, `needs ${PY} with websockets`);
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto('/');
  await page.getByRole('button', { name: 'More sources…' }).click();
  await page.locator('#bridge-url').fill(`ws://localhost:${PORT}`);
  await page.getByRole('button', { name: 'Connect to Python' }).click();
  await expect(page.locator('.header')).toContainText('Simulated DAQ (Python)');
  await page.getByRole('button', { name: '← Back to monitor' }).click();
  await expect(page.locator('[data-testid=tile-rr] .val')).toHaveText(/^1[4-6]$/, { timeout: 25_000 });
  await expect(page.locator('[data-testid=tile-etco2] .val')).toHaveText(/^3[6-9]$/);
  await expect(page.locator('[data-testid=tile-vt] .val')).toHaveText(/^(4[6-9]\d|5[0-4]\d)$/);

  // the dashboard's commands reach the Python server, and the hardware check passes
  await page.getByRole('button', { name: /Settings/ }).click();
  await page.getByRole('button', { name: 'Diagnostics' }).click();
  await page.getByRole('button', { name: 'Run hardware check' }).click();
  await expect(page.locator('.verdict')).toBeVisible({ timeout: 20_000 });
  const checks = page.locator('table.checks');
  await expect(checks.locator('tr', { hasText: 'Firmware handshake' })).toContainText('PASS');
  await expect(checks.locator('tr', { hasText: 'Firmware handshake' })).toContainText(/co2r-py-\d/);
  await expect(checks.locator('tr', { hasText: 'Dropped samples' })).toContainText('PASS');
  await expect(checks.locator('tr', { hasText: 'Corrupt lines' })).toContainText('PASS');
  expect(errors).toEqual([]);
});
