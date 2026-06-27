/**
 * Spec 3: owner invites a teammate, captures the token, the public
 * preview renders without auth.
 *
 * Maps to Aşama C step 4 "Settings → Members → invite". The
 * "invitee accepts" half of the flow needs a second logged-in
 * browser context + the token from the POST response — we stash the
 * token off the network capture and feed it into an incognito
 * context.
 */

import { expect, test } from '@playwright/test';

test('owner invites a teammate and the public preview is reachable', async ({
  page,
}) => {
  const baseUrl = process.env.E2E_BASE_URL;
  const email = process.env.E2E_TEST_EMAIL;
  const password = process.env.E2E_TEST_PASSWORD;
  test.skip(
    !baseUrl || !email || !password,
    'E2E env vars not set (E2E_BASE_URL / E2E_TEST_EMAIL / E2E_TEST_PASSWORD)',
  );

  // Log in as the operator's pilot owner.
  await page.goto('/tr/login');
  await page.getByLabel(/e-?posta/i).fill(email as string);
  await page.getByLabel(/şifre/i).fill(password as string);
  await page.getByRole('button', { name: /giriş|oturum/i }).click();
  await expect(page).toHaveURL(/\/tr\/dashboard/, { timeout: 15_000 });

  // Capture the token by sniffing the POST response.
  const tokenPromise = page.waitForResponse(
    (response) =>
      response.url().includes('/api/v1/tenant/invitations') &&
      response.request().method() === 'POST',
  );

  await page.goto('/tr/settings/invitations');
  const inviteEmail = `e2e-invitee-${Date.now()}@example.com`;
  await page.getByLabel(/e-?posta/i).fill(inviteEmail);
  await page.getByRole('button', { name: /davet|invite/i }).click();

  const response = await tokenPromise;
  expect(response.status()).toBe(201);
  const body = (await response.json()) as { token: string };
  expect(body.token.length).toBeGreaterThan(20);

  // Pending list should now include the invitee email.
  await expect(page.getByText(inviteEmail)).toBeVisible();

  // Public preview is reachable WITHOUT auth — open it in a new context.
  const incognito = await page.context().browser()?.newContext();
  if (!incognito) {
    throw new Error('Playwright failed to spin up an incognito context');
  }
  try {
    const previewPage = await incognito.newPage();
    await previewPage.goto(`/tr/invitations/${body.token}`);
    await expect(previewPage.getByText(inviteEmail)).toBeVisible();
  } finally {
    await incognito.close();
  }
});
