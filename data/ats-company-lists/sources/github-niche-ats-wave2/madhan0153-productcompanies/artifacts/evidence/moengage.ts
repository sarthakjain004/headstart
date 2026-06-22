import { chromium } from "playwright";
import type { CompanyConfig } from "./_types.js";
import type { RawJob } from "@prodmatch/shared";
import { enrichDescriptions, sleep } from "./_types.js";
import { INDIA_RE } from "./_simple.js";

interface TrakstarLink {
  href: string;
  text: string;
}

function parseLink(link: TrakstarLink): RawJob | null {
  if (!/\/jobs\/fk/i.test(link.href)) return null;
  const parts = link.text.split("\n").map((part) => part.trim()).filter(Boolean);
  if (parts.length < 2) return null;
  const title = parts[0];
  const location = parts.slice(1).find((part) => INDIA_RE.test(part) || /Bengaluru|Mumbai/i.test(part)) ?? "";
  if (!INDIA_RE.test(location) && !/Bengaluru|Mumbai/i.test(location)) return null;
  return {
    external_id: link.href.match(/\/jobs\/([^/]+)/)?.[1] ?? link.href,
    title,
    location_raw: location,
    description: "",
    apply_url: link.href,
    raw: { text: link.text },
  };
}

export const moengageConfig: CompanyConfig = {
  slug: "moengage",
  async crawl(ctx): Promise<RawJob[]> {
    const browser = await chromium.launch({ headless: true, args: ["--no-sandbox", "--disable-setuid-sandbox"] });
    try {
      const context = await browser.newContext();
      const page = await context.newPage();
      await page.goto("https://moengage.hire.trakstar.com/jobs/", { waitUntil: "domcontentloaded", timeout: 45_000 });
      await sleep(1200);
      const links = await page.evaluate(() =>
        Array.from(document.querySelectorAll("a"))
          .map((a) => ({ href: (a as HTMLAnchorElement).href, text: (a.textContent ?? "").trim() }))
          .filter((link) => link.text.length > 0),
      ) as TrakstarLink[];
      const jobs = links.map(parseLink).filter((job): job is RawJob => job !== null);

      await enrichDescriptions({ page, log: ctx.log }, jobs, () => {
        const selectors = [".job-description", ".description", "[class*='description'i]", "main", "body"];
        for (const selector of selectors) {
          const text = (document.querySelector(selector)?.textContent ?? "").trim();
          if (text.length >= 100) return Promise.resolve(text);
        }
        return Promise.resolve("");
      }, { waitUntil: "domcontentloaded", extraWaitMs: 800, timeoutMs: 18_000, concurrency: 3 });

      ctx.log(`Trakstar [moengage] India jobs: ${jobs.length}`);
      return jobs;
    } finally {
      await browser.close().catch(() => {});
    }
  },
};
