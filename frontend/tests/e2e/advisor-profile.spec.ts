import { expect, test } from "@playwright/test";
import { signUp } from "../support/account";
import { mockApi } from "../support/mockApi";

/* A member personalises the AI Advisor: the questionnaire is scored by the server, the profile shows above the chat,
   and every answer is kept in their own advice history. (The AI itself is mocked — see mockApi.) */

test.beforeEach(async ({ page }) => { await mockApi(page); });

test("a member sets an investor profile and finds their advice history", async ({ page }) => {
  await signUp(page, "ip");
  await page.goto("/advisor");
  await page.getByRole("button", { name: "Personalise the advisor" }).click();
  await page.getByLabel("retirement").check();
  await page.getByLabel("When will you need this money?").selectOption({ label: "over 7 years" });
  await page.getByLabel("Monthly amount you can invest (AED, optional)").fill("2000");
  for (const option of ["Hold, and maybe add a little", "Growth over time", "Stable, with savings for emergencies", "Some (funds, a few shares)", "A small part"]) {
    await page.getByLabel(option).check();
  }
  await page.getByRole("button", { name: "Save profile" }).click();
  await expect(page.getByText("Tailored to your profile:")).toContainText("Growth");
  await expect(page.getByText("Tailored to your profile:")).toContainText("AED 2,000/month");

  await page.getByRole("link", { name: "Your advice history" }).click();
  await expect(page).toHaveURL(/\/advisor\/history$/);
  await expect(page.locator("main h1")).toHaveText("Your advice history");
});
