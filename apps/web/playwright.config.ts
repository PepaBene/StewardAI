import { defineConfig } from "@playwright/test";

/*
 * The route walk (e2e/route-walk.spec.ts): visits every page of the app on a running deployment
 * and records layout and content metrics. It never starts a server; point it at one:
 *
 *   BASE_URL=https://<deployment> npm run test:routes
 *
 * Sign-in: the app needs a password and an MFA code, so the owner saves a signed-in browser
 * state once (gitignored, see e2e/route-walk.spec.ts):
 *   npx playwright codegen --save-storage=e2e/.auth/state.json $BASE_URL
 */
export default defineConfig({
  testDir: "./e2e",
  testMatch: /.*\.spec\.ts/,
  outputDir: "./test-results",
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  timeout: 90_000,
  workers: 1,
  fullyParallel: false,
  use: {
    baseURL: process.env.BASE_URL,
    storageState: "e2e/.auth/state.json",
    trace: "off",
  },
});
