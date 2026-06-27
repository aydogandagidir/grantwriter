/**
 * Spec 1: signup → onboarding wizard → dashboard.
 *
 * Maps to Aşama C step 4 "Sign up → confirm email → log in → set up
 * workspace". The email-confirm round-trip is intentionally not
 * automated here — operators run this spec with a Supabase project
 * that has email confirmation disabled.
 *
 * Skips when the operator hasn't set ``E2E_BASE_URL`` /
 * ``E2E_TEST_EMAIL`` / ``E2E_TEST_PASSWORD``.
 */

import { expect, test } from '@playwright/test';

test('a brand-new user can sign up and create their first workspace', async ({
  page,
}) => {
  const baseUrl = process.env.E2E_BASE_URL;
  const baseEmail = process.env.E2E_TEST_EMAIL;
  const password = process.env.E2E_TEST_PASSWORD;
  test.skip(
    !baseUrl || !baseEmail || !password,
    'E2E env vars not set (E2E_BASE_URL / E2E_TEST_EMAIL / E2E_TEST_PASSWORD)',
  );

  // The operator's account becomes the seed for per-run unique emails.
  const at = (baseEmail as string).indexOf('@');
  const local = (baseEmail as string).slice(0, at);
  const domain = (baseEmail as string).slice(at + 1);
  const suffix = Math.random().toString(36).slice(2, 10);
  const randomEmail = `${local}+e2e-${suffix}@${domain}`;

  await page.goto('/tr/signup');

  await page.getByLabel(/e-?posta/i).fill(randomEmail);
  await page.getByLabel(/şifre/i).fill(password as string);
  await page.getByLabel(/ad/i).first().fill('E2E Smoke User');
  await page.getByRole('button', { name: /kayıt|kaydol/i }).click();

  await expect(page).toHaveURL(/\/tr\/onboarding/, { timeout: 15_000 });
  await expect(page.getByText(/workspace'?ini kur/i)).toBeVisible();

  // Step 1 — name + slug + language.
  await page.getByLabel(/workspace adı/i).fill('E2E Smoke Workspace');
  await page.getByLabel(/url slug/i).fill(`e2e-${Date.now().toString(36)}`);
  await page.getByRole('button', { name: /devam/i }).click();

  // Step 2 — confirm starter plan, submit.
  await expect(page.getByText(/başlangıç planı/i)).toBeVisible();
  await page.getByRole('button', { name: /workspace oluştur/i }).click();

  await expect(page).toHaveURL(/\/tr\/dashboard/, { timeout: 15_000 });
  await expect(page.getByRole('navigation')).toContainText(/E2E Smoke/);
});
