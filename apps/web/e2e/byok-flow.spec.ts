/**
 * Spec 2: BYOK setup → test → status badges.
 *
 * Maps to Aşama C step 4 "Settings → BYOK → store an Anthropic test
 * key". Assumes the operator has a real Anthropic test key in
 * ``E2E_ANTHROPIC_TEST_KEY``; the test endpoint validates against
 * Anthropic and a fake key would 401 the smoke run.
 *
 * Pre-condition: the test account already exists + has a workspace
 * (run after the signup spec or reuse the operator's pilot tenant).
 */

import { expect, test } from '@playwright/test';

test('owner stores an Anthropic key and the test endpoint returns valid', async ({
  page,
}) => {
  const baseUrl = process.env.E2E_BASE_URL;
  const email = process.env.E2E_TEST_EMAIL;
  const password = process.env.E2E_TEST_PASSWORD;
  const anthropicKey = process.env.E2E_ANTHROPIC_TEST_KEY;
  test.skip(
    !baseUrl || !email || !password,
    'E2E env vars not set (E2E_BASE_URL / E2E_TEST_EMAIL / E2E_TEST_PASSWORD)',
  );
  test.skip(
    !anthropicKey,
    'E2E_ANTHROPIC_TEST_KEY not set — skipping BYOK roundtrip',
  );

  // Log in via the standard /login flow.
  await page.goto('/tr/login');
  await page.getByLabel(/e-?posta/i).fill(email as string);
  await page.getByLabel(/şifre/i).fill(password as string);
  await page.getByRole('button', { name: /giriş|oturum/i }).click();
  await expect(page).toHaveURL(/\/tr\/dashboard/, { timeout: 15_000 });

  // Settings → LLM config.
  await page.goto('/tr/settings/llm-config');
  await expect(page.getByText(/BYOK|API anahtar/i)).toBeVisible();

  // Fill the Anthropic field + save.
  await page
    .getByLabel(/anthropic/i)
    .first()
    .fill(anthropicKey as string);
  await page.getByRole('button', { name: /kaydet/i }).click();
  await expect(page.getByText(/kaydedildi|saved/i)).toBeVisible({
    timeout: 10_000,
  });

  // The status badge flips to "configured" after a successful save.
  await expect(page.getByText(/anthropic.*yapılandırıldı|configured/i)).toBeVisible();

  // Test button runs a 5-token Claude Sonnet ping.
  await page.getByRole('button', { name: /test|deneme/i }).click();
  await expect(page.getByText(/geçerli|valid/i)).toBeVisible({ timeout: 15_000 });
});
