import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests', testMatch: 'reader.spec.ts', timeout: 60_000,
  fullyParallel: false, workers: 1,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: { baseURL: 'http://127.0.0.1:1420', browserName: 'chromium', colorScheme: 'light', screenshot: 'only-on-failure', trace: 'retain-on-failure' },
  webServer: { command: 'npm run build:web && npm run preview', url: 'http://127.0.0.1:1420', reuseExistingServer: false, timeout: 60_000 },
});
