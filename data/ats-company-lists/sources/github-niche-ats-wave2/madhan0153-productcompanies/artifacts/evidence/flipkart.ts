import type { CompanyConfig, CrawlContext } from "./_types.js";
import type { RawJob } from "@prodmatch/shared";
import { sleep, enrichDescriptions } from "./_types.js";

// Flipkart uses TurboHire platform.
// Listings: dashboard page loads filteredjobs API with a Bearer JWT obtained
// by visiting the page → intercept via Playwright.
// Detail: TurboHire's public job-details URL deep-links into the dashboard
// SPA which renders the full JD body. The `JobDescription` field returned
// by filteredjobs is truncated to a teaser (we observed 22-39 chars).
const ORG_ID = "4d757ba0-3d57-448a-b82c-238ed87ac90f";
const CAREER_URL = `https://flipkart.turbohire.co/dashboardv2?orgId=${ORG_ID}&type=0`;
// TurboHire's public job-detail URL. The dashboardv2 deep-link we tried
// previously didn't render the JD body for unauthenticated visitors.
// `/jobdetails` is the one the "View" button in the listing actually opens.
const detailUrl = (jobId: string) =>
  `https://flipkart.turbohire.co/jobdetails?orgId=${ORG_ID}&jobId=${jobId}`;

interface TurboLocation {
  Address?: string;
}

interface TurboJob {
  JobId: string;
  JobCode: string;
  JobTitle: string;
  Department?: string;
  Location?: string | TurboLocation[];
  City?: string;
  PublishedDate?: string;
  ExpiryDates?: Record<string, string>;
  JobDescription?: string;
}

interface TurboResponse {
  Total: number;
  Result: TurboJob[];
}

function extractTurboCity(loc: string | TurboLocation[] | undefined): string | undefined {
  if (!loc) return undefined;
  if (typeof loc === "string") return loc || undefined;
  // Array of location objects — extract city from Address field
  const addr = loc[0]?.Address;
  if (!addr) return undefined;
  // Address: "Alok Clinic, Sector 1A, New Panvel East, Panvel, Maharashtra, India"
  // Use the second-to-last comma-segment as city approximation, or first segment
  const parts = addr.split(",").map((s) => s.trim()).filter(Boolean);
  return parts.length >= 2 ? parts[parts.length - 2] : parts[0];
}

export const flipkartConfig: CompanyConfig = {
  slug: "flipkart",
  async crawl({ page, log }: CrawlContext): Promise<RawJob[]> {
    const jobs: RawJob[] = [];

    // Intercept the filteredjobs API response (JWT obtained automatically by page load)
    const responsePromise = page.waitForResponse(
      (resp) => resp.url().includes("careerpagev2/filteredjobs") && resp.status() === 200,
      { timeout: 45_000 },
    );

    await page.goto(CAREER_URL, { waitUntil: "load", timeout: 60_000 });

    try {
      const resp = await responsePromise;
      const data = (await resp.json()) as TurboResponse;
      const batch = data.Result ?? [];
      log(`Total: ${data.Total} jobs`);

      for (const j of batch) {
        const location = j.City ?? extractTurboCity(j.Location) ?? "Bengaluru";
        // The listing API truncates JobDescription to a 22-39 char teaser.
        // Drop it so enrichDescriptions kicks in (its <60 char gate).
        jobs.push({
          external_id: j.JobId,
          title: j.JobTitle,
          location_raw: location,
          description: "",
          apply_url: detailUrl(j.JobId),
          posted_at: j.PublishedDate,
          raw: j as unknown as Record<string, unknown>,
        });
      }
    } catch (err) {
      log(`API interception failed: ${err instanceof Error ? err.message : String(err)}`, "warn");
      // Fallback: wait and retry once
      await sleep(5000);
      const fallbackResp = await page.waitForResponse(
        (resp) => resp.url().includes("careerpagev2/filteredjobs"),
        { timeout: 30_000 },
      ).catch(() => null);
      if (fallbackResp) {
        const data = (await fallbackResp.json()) as TurboResponse;
        for (const j of (data.Result ?? [])) {
          jobs.push({
            external_id: j.JobId,
            title: j.JobTitle,
            location_raw: j.City ?? extractTurboCity(j.Location) ?? "Bengaluru",
            description: "",
            apply_url: detailUrl(j.JobId),
            posted_at: j.PublishedDate,
            raw: j as unknown as Record<string, unknown>,
          });
        }
      }
    }

    log(`Total: ${jobs.length} India jobs`);

    // Pull full JD body. TurboHire's SPA paints the JD into the dashboard
    // shell when ?jobId=... is in the URL. networkidle never fires (their
    // analytics ping every few seconds), which caused 0/12 with all
    // 35s timeouts in the 2026-05-17 run. Domcontentloaded + a longer grace
    // period reaches the painted JD reliably.
    await enrichDescriptions({ page, log }, jobs, () => {
      const tryEls = [
        "[class*='job-detail'i]",
        "[class*='description'i]",
        "[data-testid*='job'i]",
        "main",
        "[role='main']",
        "body",
      ];
      for (const sel of tryEls) {
        const el = document.querySelector(sel);
        const text = (el?.textContent ?? "").trim();
        if (text.length >= 200) return Promise.resolve(text);
      }
      return Promise.resolve("");
    }, { waitUntil: "domcontentloaded", extraWaitMs: 3000, timeoutMs: 25_000 });

    return jobs;
  },
};
