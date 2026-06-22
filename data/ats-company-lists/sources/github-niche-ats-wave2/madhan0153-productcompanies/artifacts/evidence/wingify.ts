import type { CompanyConfig } from "./_types.js";
import type { RawJob } from "@prodmatch/shared";
import { fetchJson } from "../lib/http.js";
import { INDIA_RE, stripHtml } from "./_simple.js";

interface KekaJob {
  id: number;
  title?: string;
  description?: string;
  department?: string;
  jobType?: string;
  locations?: Array<{ city?: string; state?: string; country?: string; name?: string }>;
  location?: string;
  experience?: string;
}

function locationText(job: KekaJob): string {
  const locations = (job.locations ?? []).map((loc) =>
    loc.name ?? [loc.city, loc.state, loc.country].filter(Boolean).join(", "),
  );
  return [job.location, ...locations].filter(Boolean).join(" / ");
}

export const wingifyConfig: CompanyConfig = {
  slug: "wingify",
  async crawl(ctx): Promise<RawJob[]> {
    const data = await fetchJson<KekaJob[]>("https://wingify.keka.com/careers/api/jobs/default/active", {
      headers: { Accept: "application/json", Referer: "https://wingify.keka.com/careers/" },
      timeoutMs: 25_000,
    });
    const jobs = data
      .filter((job) => INDIA_RE.test(locationText(job)) || locationText(job).trim() === "")
      .map((job): RawJob => ({
        external_id: String(job.id),
        title: job.title ?? `Wingify role ${job.id}`,
        location_raw: locationText(job) || "Remote-India",
        description: stripHtml(job.description),
        apply_url: `https://wingify.keka.com/careers/jobdetails/${job.id}`,
        raw: job as unknown as Record<string, unknown>,
      }));
    ctx.log(`Keka [wingify] India jobs: ${jobs.length}`);
    return jobs;
  },
};
