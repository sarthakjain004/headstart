import { chromium } from "playwright";
import type { CompanyConfig, CrawlContext } from "./_types.js";
import type { RawJob } from "@prodmatch/shared";
import type { ElementHandle, Page } from "playwright";
import { sleep, enrichDescriptions } from "./_types.js";
import { resolveAdaptive, type AdaptiveTelemetry } from "../lib/adaptive.js";
import { loadReferenceSignatures } from "../lib/fixture-store.js";

// Zomato (now part of "Eternal") publishes openings via Zoho Recruit:
//   https://eternal.zohorecruit.in/jobs/Careers
// Their own marketing pages (zomato.com/careers, eternal.com/careers/openings)
// are SPA shells with no actual listings. www.zomato.com itself sits behind
// Akamai which rejects Playwright's HTTP/2 fingerprint with
// ERR_HTTP2_PROTOCOL_ERROR — but the Zoho subdomain isn't behind that
// defence, so a normal Playwright session works there. We still launch a
// dedicated Chromium with --disable-http2 in case Zoho fronts their CDN
// the same way later.
//
// DOM (confirmed via smoke test):
//   .cw-filter-joblist          — one element per job
//   .cw-3-title                 — anchor with title text + href to /jobs/Careers/{id}/{slug}
//   .cw-filter-joblist-right    — type + posted date

const PORTAL_URL = "https://eternal.zohorecruit.in/jobs/Careers";

const CARD_LABEL = "job_card";
const CARD_SELECTOR = ".cw-filter-joblist";

interface ZomatoRaw {
  title: string;
  href: string;
  right: string;
  fullTitle: string;
  location: string;
}

export const zomatoConfig: CompanyConfig = {
  slug: "zomato",
  async crawl(_ctx: CrawlContext): Promise<RawJob[]> {
    const log = _ctx.log;

    let browser;
    try {
      browser = await chromium.launch({
        headless: true,
        args: [
          "--no-sandbox",
          "--disable-setuid-sandbox",
          "--disable-http2",
          "--disable-blink-features=AutomationControlled",
        ],
      });
    } catch (err) {
      log(`failed to launch dedicated browser: ${(err as Error).message}`, "error");
      return [];
    }

    // Adaptive reference signatures — empty until a fixture is captured.
    const cardReference = loadReferenceSignatures("zomato", CARD_LABEL);

    const onTelemetry = (e: AdaptiveTelemetry) => {
      if (e.kind === "fallback_hit") {
        log(
          `[adaptive] ${e.label}: CSS failed; recovered ${e.count} via fingerprint match (mean confidence ${e.meanConfidence.toFixed(2)})`,
          "warn",
        );
      } else if (e.kind === "miss") {
        log(`[adaptive] ${e.label}: no recovery (${e.reason})`, "warn");
      }
    };

    try {
      const browserCtx = await browser.newContext({
        userAgent:
          "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        extraHTTPHeaders: {
          "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
          "Accept-Language": "en-US,en;q=0.9",
        },
        viewport: { width: 1366, height: 900 },
      });
      const page = await browserCtx.newPage();

      try {
        await page.goto(PORTAL_URL, { waitUntil: "domcontentloaded", timeout: 60_000 });
      } catch (err) {
        log(`nav failed: ${(err as Error).message.split("\n")[0]}`, "error");
        return [];
      }

      // Zoho Recruit fetches the job list via XHR after page load. Wait for
      // either a result row to appear or a "no openings" indicator.
      try {
        await page.waitForSelector(".cw-filter-joblist, .careers-nojob-text", {
          timeout: 20_000,
        });
      } catch { /* fall through — selectors may not have fired */ }
      await sleep(2000);

      // Try to expand pagination if present (Zoho's "Load more" pattern)
      for (let i = 0; i < 5; i++) {
        const more = await page.$(".cw-loadmore-btn, [class*='loadmore']");
        if (!more) break;
        try {
          await more.click({ timeout: 2000 });
          await sleep(1500);
        } catch { break; }
      }

      // Happy path.
      let rawJobs = await extractZomatoJobs(page, CARD_SELECTOR);

      // Drift fallback: Zoho Recruit ships CSS-module class renames quarterly.
      // The fingerprint match recovers when .cw-filter-joblist breaks.
      if (rawJobs.length === 0 && cardReference.length > 0) {
        const matches = await resolveAdaptive(page, {
          slug: "zomato",
          label: CARD_LABEL,
          selector: CARD_SELECTOR,
          reference: cardReference,
          onTelemetry,
        });
        if (matches.length > 0) {
          rawJobs = await extractZomatoFromHandles(matches.map((m) => m.handle));
          for (const m of matches) await m.handle.dispose();
        }
      }

      log(`Found ${rawJobs.length} listings on Zoho Recruit`);

      const jobs: RawJob[] = rawJobs.map((r) => {
        // Job ID is in the URL path: /jobs/Careers/{numeric-id}/{slug}
        const m = r.href.match(/\/Careers\/(\d+)/);
        const id = m?.[1] ?? r.href;
        return {
          external_id: id,
          title: r.title,
          location_raw: r.location,
          description: "",
          apply_url: r.href,
          raw: { fullTitle: r.fullTitle, right: r.right, href: r.href },
        };
      });

      // Visit each detail page for the full JD body. Zoho Recruit's detail
      // pages mount the description into #cw-rich-description (the rich-text
      // body) inside the .cw-jobdescription wrapper. Confirmed via DOM probe.
      await enrichDescriptions({ page, log }, jobs, () => {
        const tryEls = [
          "#cw-rich-description",
          ".cw-jobdescription",
          ".cw-jobdetails-container",
          "[class*='jobdescription'i]",
          "main",
        ];
        for (const sel of tryEls) {
          const el = document.querySelector(sel);
          const text = (el?.textContent ?? "").trim();
          if (text.length >= 100) return Promise.resolve(text);
        }
        return Promise.resolve("");
      // Zoho Recruit fires analytics XHRs every few seconds so networkidle
      // never settles; domcontentloaded + grace gets us the painted JD body
      // inside 4 seconds reliably.
      }, { waitUntil: "domcontentloaded", extraWaitMs: 2500, timeoutMs: 22_000 });

      log(`Total: ${jobs.length} jobs`);
      return jobs;
    } finally {
      try { await browser.close(); } catch { /* ignore */ }
    }
  },
};

// ── Extraction helpers ─────────────────────────────────────────────────────

async function extractZomatoJobs(page: Page, selector: string): Promise<ZomatoRaw[]> {
  return page.$$eval(selector, (rows) =>
    rows.map((row) => {
      const link = row.querySelector(".cw-3-title") as HTMLAnchorElement | null;
      const title = (link?.textContent ?? "").trim();
      const href = link?.href ?? "";
      const right = (row.querySelector(".cw-filter-joblist-right")?.textContent ?? "").trim();
      const parts = title.split(",").map((s) => s.trim());
      const cleanTitle = parts[0] ?? title;
      const location = parts.length > 1 ? parts[1] : "Gurugram";
      return { title: cleanTitle, href, right, fullTitle: title, location };
    }).filter((r) => r.title.length > 3 && r.href.length > 10),
  );
}

async function extractZomatoFromHandles(handles: ElementHandle[]): Promise<ZomatoRaw[]> {
  const out: ZomatoRaw[] = [];
  for (const h of handles) {
    const row = await h.evaluate((el) => {
      const root = el as HTMLElement;
      const link = root.querySelector(".cw-3-title") as HTMLAnchorElement | null;
      const title = (link?.textContent ?? "").trim();
      const href = link?.href ?? "";
      const right = (root.querySelector(".cw-filter-joblist-right")?.textContent ?? "").trim();
      const parts = title.split(",").map((s) => s.trim());
      const cleanTitle = parts[0] ?? title;
      const location = parts.length > 1 ? parts[1] : "Gurugram";
      return { title: cleanTitle, href, right, fullTitle: title, location };
    });
    if (row.title.length > 3 && row.href.length > 10) out.push(row);
  }
  return out;
}
