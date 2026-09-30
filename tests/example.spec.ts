import { test, expect } from '@playwright/test';

// Self-contained smoke test: renders a page in memory so it passes without internet access.
test('renders a page and follows a link', async ({ page }) => {
  await page.setContent(`
    <title>Polymarket Analysis</title>
    <a href="#intro" onclick="document.querySelector('h1').textContent = 'Installation'">Get started</a>
    <h1>Welcome</h1>
  `);

  await expect(page).toHaveTitle(/Polymarket Analysis/);

  await page.getByRole('link', { name: 'Get started' }).click();

  await expect(page.getByRole('heading', { name: 'Installation' })).toBeVisible();
});
