import { chromium } from "playwright";
import type { CompanyConfig } from "./_types.js";
import type { RawJob } from "@prodmatch/shared";
import { enrichDescriptions, sleep } from "./_types.js";
import { INDIA_RE } from "./_simple.js";

interface KulaCard {
  href: string;
  text: string;
}

function parseCard(card: KulaCard): RawJob | null {
  const lines = card.text.split("\n").map((line) => line.trim()).filter(Boolean);
  const applyIndex = lines.findIndex((line) => /^apply now$/i.test(line));
  const title = lines.find((line) => line.length > 2 && !/^apply now$/i.test(line));
  const location = lines.find((line) => INDIA_RE.test(line)) ?? "";
  if (!title || !INDIA_RE.test(location)) return null;
  return {
    external_id: card.href.match(/clevertap\/(\d+)/)?.[1] ?? card.href,
    title,
    location_raw: location,
    description: "",
    apply_url: card.href,
    raw: { text: card.text, applyIndex },
  };
}

export const clevertapConfig: CompanyConfig = {
  slug: "clevertap",
  async crawl(ctx): Promise<RawJob[]> {
    const browser = await chromium.launch({ headless: true, args: ["--no-sandbox", "--disable-setuid-sandbox"] });
    try {
      const context = await browser.newContext();
      const page = await context.newPage();
      await page.goto("https://careers.kula.ai/clevertap", { waitUntil: "domcontentloaded", timeout: 45_000 });
      await sleep(3000);

      const cards = await page.evaluate(() =>
        Array.from(document.querySelectorAll('a[href*="/clevertap/"]'))
          .map((anchor) => {
            let current: HTMLElement | null = anchor as HTMLElement;
            for (let depth = 0; depth < 6 && current?.parentElement; depth++) {
              current = current.parentElement;
              const text = (current.innerText ?? current.textContent ?? "").trim();
              if (/Apply Now/i.test(text) && /India|Mumbai|Bengaluru|Bangalore/i.test(text)) {
                return { href: (anchor as HTMLAnchorElement).href, text };
              }
            }
            const parent = anchor.parentElement;
            return { href: (anchor as HTMLAnchorElement).href, text: ((parent?.innerText ?? parent?.textContent) ?? "").trim() };
          }),
      ) as KulaCard[];

      const seen = new Set<string>();
      const jobs = cards
        .map(parseCard)
        .filter((job): job is RawJob => job !== null)
        .filter((job) => {
          if (seen.has(job.external_id)) return false;
          seen.add(job.external_id);
          return true;
        });

      await enrichDescriptions({ page, log: ctx.log }, jobs, () => {
        const selectors = ["[data-testid*='description']", "[class*='description'i]", "main", "body"];
        for (const selector of selectors) {
          const text = (document.querySelector(selector)?.textContent ?? "").trim();
          if (text.length >= 120) return Promise.resolve(text);
        }
        return Promise.resolve("");
      }, { waitUntil: "domcontentloaded", extraWaitMs: 1000, timeoutMs: 18_000, concurrency: 3 });

      ctx.log(`Kula [clevertap] India jobs: ${jobs.length}`);
      return jobs;
    } finally {
      await browser.close().catch(() => {});
    }
  },
};
