import { expect, test, type Page } from '@playwright/test';

const tileValue = (page: Page, id: string) => page.locator(`[data-testid=${id}] .val`);

test.beforeEach(async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  (page as unknown as { errors: string[] }).errors = errors;
});

test('simulator drives waveforms and numbers', async ({ page }) => {
  await page.goto('/?sim');
  await expect(page.locator('.header')).toContainText('Simulator');
  // a few breaths at 15/min
  await expect(tileValue(page, 'tile-rr')).toHaveText(/^1[4-6]$/, { timeout: 20_000 });
  await expect(tileValue(page, 'tile-etco2')).toHaveText(/^3[6-9]$/);
  await expect(tileValue(page, 'tile-vt')).toHaveText(/^(4[6-9]\d|5[0-4]\d)$/);
  expect((page as unknown as { errors: string[] }).errors).toEqual([]);
});

test('starts disconnected with a clear prompt', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'No signal source' })).toBeVisible();
  await page.getByRole('button', { name: 'Start simulator' }).click();
  await expect(page.getByRole('heading', { name: 'No signal source' })).toBeHidden();
});

test('test alarm shows the high-priority banner', async ({ page }) => {
  await page.goto('/?sim');
  await page.getByRole('button', { name: /Alarm limits/ }).click();
  await page.getByRole('button', { name: 'Test alarm' }).click();
  await expect(page.locator('.alarm-slot')).toContainText('TEST ALARM');
  await expect(page.locator('.alarm-slot')).toHaveClass(/high/);
});

test('CO2 ramp raises the EtCO2 HIGH alarm at the set limit', async ({ page }) => {
  await page.goto('/?sim');
  await page.getByRole('button', { name: /Alarm limits/ }).click();
  const high = page.locator('#drawer-high');
  await high.fill('41');
  await high.press('Enter');
  await page.getByRole('button', { name: 'Done' }).click();
  await page.getByRole('button', { name: /Settings/ }).click();
  await page.locator('#scenario').selectOption('co2Ramp');
  await page.getByRole('button', { name: '← Back to monitor' }).click();
  await expect(page.locator('.alarm-slot')).toContainText('EtCO2 HIGH', { timeout: 45_000 });
  await expect(page.locator('[data-testid=tile-etco2]')).toHaveClass(/alarm-high/);
  await page.getByRole('button', { name: 'ACKNOWLEDGE' }).click();
  await expect(page.locator('.pill')).toContainText('SILENCED');
});

test('light and dark themes', async ({ page }) => {
  await page.goto('/?sim');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.getByRole('button', { name: /Settings/ }).click();
  await page.getByRole('button', { name: 'Display & units' }).click();
  await page.getByRole('button', { name: 'Light', exact: true }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  expect(bg).not.toBe('rgb(0, 0, 0)');
});

test('manual calibration: edit, preview, save', async ({ page }) => {
  await page.goto('/?sim');
  await page.getByRole('button', { name: /Settings/ }).click();
  await page.getByRole('button', { name: 'Calibration' }).click();
  const card = page.locator('section.card.co2');
  await expect(card).toContainText('CO2 (capnograph)');
  const gain = card.locator('#co2-gain');
  await gain.fill('4');
  await gain.press('Enter');
  await expect(card).toContainText('Preview (unsaved)');
  await expect(card).toContainText('Unsaved changes');
  await card.getByRole('button', { name: 'Save calibration' }).click();
  await expect(card).toContainText(/saved \d/);
  // persists across reload
  await page.reload();
  await page.getByRole('button', { name: /Settings/ }).click();
  await page.getByRole('button', { name: 'Calibration' }).click();
  await expect(page.locator('section.card.co2 #co2-gain')).toHaveValue('4');
});

test('zero & span capture computes a line from live readings', async ({ page }) => {
  await page.goto('/?sim');
  await page.getByRole('button', { name: /Settings/ }).click();
  await page.getByRole('button', { name: 'Calibration' }).click();
  const card = page.locator('section.card.flow');
  await card.getByRole('tab', { name: 'Two-point' }).click();
  const capture = card.getByRole('button', { name: 'Capture' });
  await capture.first().click();
  // type point B while point A is still capturing: the capture must not undo it
  await card.getByLabel('Reference flow volts').fill('3.25');
  await card.getByLabel('Reference flow volts').press('Enter');
  await expect(card.getByLabel('No flow volts')).not.toHaveValue('', { timeout: 5000 });
  await expect(card.getByLabel('Reference flow volts')).toHaveValue('3.25');
  await card.getByRole('button', { name: 'Compute line' }).click();
  await expect(card).toContainText('Line through 2 points');
});

test('record → one file saved on stop → import → review → export', async ({ page }) => {
  await page.goto('/?sim');
  await expect(page.locator('[data-testid=tile-rr] .val')).toHaveText(/^\d+$/, { timeout: 20_000 });
  await page.getByRole('button', { name: /Record/ }).click();
  await expect(page.locator('.header .rec')).toContainText('REC');
  await page.keyboard.press('m'); // a marker inside the recording
  await page.getByRole('button', { name: 'Save label' }).click();
  await page.waitForTimeout(9000);

  const dl = page.waitForEvent('download');
  await page.getByRole('button', { name: /Stop rec/ }).click();
  const download = await dl;
  expect(download.suggestedFilename()).toMatch(/^co2r_STUDY-01_\d{8}-\d{6}\.csv$/);
  const path = await download.path();
  const fs = await import('node:fs');
  const text = fs.readFileSync(path, 'utf8');
  expect(text.startsWith('# CO2-Response session,v2')).toBe(true);
  expect(text).toContain('MARK Event 1');
  expect(text).toContain('RECORDING_STOPPED');

  // the recording is in the library
  await page.getByRole('button', { name: /Settings/ }).click();
  await page.getByRole('button', { name: 'Data & recordings' }).click();
  const table = page.locator('table.sessions');
  await expect(table.locator('tbody tr')).toHaveCount(1);
  await expect(table).toContainText('Recorded');

  // import the downloaded file back
  await page.locator('input[type=file][accept=".csv,text/csv"]').setInputFiles({ name: 'reimported.csv', mimeType: 'text/csv', buffer: Buffer.from(text) });
  await expect(table.locator('tbody tr')).toHaveCount(2);
  await expect(table).toContainText('reimported.csv');
  await expect(table).toContainText('Imported');

  // review it
  await table.locator('tr', { hasText: 'reimported.csv' }).getByRole('button', { name: 'Open' }).click();
  await expect(page.locator('.review')).toContainText('reimported.csv');
  await expect(page.locator('.review .stats')).toContainText('Breaths');
  await expect(page.locator('.review .loglist')).toContainText('MARK Event 1');
  expect(await page.locator('.review-chart canvas').count()).toBeGreaterThanOrEqual(3);

  // export the breath table
  const dl2 = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Breaths (.csv)', exact: true }).click();
  const b = await dl2;
  expect(b.suggestedFilename()).toBe('reimported_breaths.csv');
  expect(fs.readFileSync(await b.path(), 'utf8')).toMatch(/^breath,time_s,wall_time_iso,etco2_mmHg/);

  // zoom by clicking an event, then export that range
  await page.locator('.review .loglist .linkish').first().click();
  await expect(page.getByRole('button', { name: 'Show whole session' })).toBeEnabled();
  const dl3 = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Zoomed range, all data (.csv)' }).click();
  expect((await dl3).suggestedFilename()).toMatch(/^reimported_\d+-\d+s\.csv$/);
  expect((page as unknown as { errors: string[] }).errors).toEqual([]);
});

test('rejects a file that is not a session', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'More sources…' }).click();
  await page.getByRole('button', { name: 'Data & recordings' }).click();
  await page.locator('input[type=file][accept=".csv,text/csv"]').setInputFiles({ name: 'people.csv', mimeType: 'text/csv', buffer: Buffer.from('name,age\nx,1\n') });
  await expect(page.locator('.toast')).toContainText('Could not import people.csv');
  await expect(page.locator('table.sessions')).toHaveCount(0);
});
