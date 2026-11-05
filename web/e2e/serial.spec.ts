import { expect, test } from '@playwright/test';
import { fakeSerialScript } from './fakeSerial';

type Fake = { port: { commands: string[]; opens: number; unplug(): void; replug(): void }; serial: { requests: number } };

test('Arduino over Web Serial: connect, hardware check, unplug, auto-reconnect, one-click reconnect', async ({ page }) => {
  test.setTimeout(120_000);
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.addInitScript(fakeSerialScript);
  await page.goto('/');
  const f = () => page.evaluate(() => (window as unknown as { __fake: Fake }).__fake);

  // connect through the port picker
  await page.getByRole('button', { name: 'Connect Arduino (USB)' }).click();
  await expect(page.locator('.header')).toContainText('Arduino (USB 2341:0043)');
  await expect(page.locator('[data-testid=tile-rr] .val')).toHaveText(/^1[4-6]$/, { timeout: 25_000 });
  await expect(page.locator('[data-testid=tile-etco2] .val')).toHaveText(/^3[6-9]$/);
  expect((await f()).port.commands).toEqual(['INFO', 'START']);

  // hardware check
  await page.getByRole('button', { name: /Settings/ }).click();
  await page.getByRole('button', { name: 'Diagnostics' }).click();
  await page.getByRole('button', { name: 'Run hardware check' }).click();
  await expect(page.locator('.verdict')).toBeVisible({ timeout: 20_000 });
  const checks = page.locator('table.checks');
  await expect(checks.locator('tr', { hasText: 'Firmware handshake' })).toContainText('PASS');
  await expect(checks.locator('tr', { hasText: 'Firmware handshake' })).toContainText('ADS1115 (I2C 0x48, ±6.144 V range)');
  await expect(checks.locator('tr', { hasText: 'Dropped samples' })).toContainText('PASS');
  await expect(checks.locator('tr', { hasText: 'Corrupt lines' })).toContainText('PASS');
  await page.getByRole('button', { name: '← Back to monitor' }).click();

  // unplug: technical alarm, then plug back in: reconnects with no click and no picker
  await page.evaluate(() => (window as unknown as { __fake: Fake }).__fake.port.unplug());
  await expect(page.locator('.alarm-slot')).toContainText('DEVICE DISCONNECTED');
  await expect(page.getByText('Waiting for the Arduino to be plugged back in…')).toBeVisible();
  await page.evaluate(() => (window as unknown as { __fake: Fake }).__fake.port.replug());
  await expect(page.locator('.header')).toContainText(/Arduino \(USB 2341:0043\)\s*·\s*\d+ Hz/, { timeout: 10_000 });
  await expect(page.locator('.alarm-slot')).toContainText('resolved');
  await expect(page.locator('[data-testid=tile-rr] .val')).toHaveText(/^1[4-6]$/, { timeout: 25_000 });
  expect((await f()).serial.requests).toBe(1);
  expect((await f()).port.opens).toBe(2);

  // user disconnects, then reconnects with one click and no picker
  await page.getByRole('button', { name: /Settings/ }).click();
  await page.getByRole('button', { name: 'Connection', exact: true }).click();
  await page.getByRole('button', { name: 'Disconnect' }).click();
  await page.getByRole('button', { name: '← Back to monitor' }).click();
  await page.getByRole('button', { name: 'Reconnect Arduino (USB 2341:0043)' }).click();
  await expect(page.locator('.header')).toContainText(/Arduino \(USB 2341:0043\)\s*·\s*\d+ Hz/);
  expect((await f()).serial.requests).toBe(1);
  expect((await f()).port.opens).toBe(3);
  expect(errors).toEqual([]);
});
