import type { CompanyConfig } from "./_types.js";
import type { RawJob } from "@prodmatch/shared";
import { fetchJson } from "../lib/http.js";
import { INDIA_RE, stripHtml } from "./_simple.js";

interface AinterviewJob {
  id: number;
  title?: string;
  description?: string;
  location?: string;
  category?: string;
  job_type?: string;
  posted_date?: string;
  apply_url?: string;
}

interface AinterviewResponse {
  jobs?: AinterviewJob[];
}

export const lenskartConfig: CompanyConfig = {
  slug: "lenskart",
  async crawl(ctx): Promise<RawJob[]> {
    const data = await fetchJson<AinterviewResponse>("https://ainterviews.com/api/job_board/lenskart_ho/jobs/", {
      headers: { Accept: "application/json", Referer: "https://careers.lenskart.com/" },
      timeoutMs: 25_000,
    });
    const jobs = (data.jobs ?? [])
      .filter((job) => INDIA_RE.test(job.location ?? ""))
      .map((job): RawJob => ({
        external_id: String(job.id),
        title: job.title ?? `Lenskart role ${job.id}`,
        location_raw: job.location ?? "",
        description: stripHtml(job.description),
        apply_url: job.apply_url ? new URL(job.apply_url, "https://ainterviews.com").toString() : undefined,
        posted_at: job.posted_date,
        raw: job as unknown as Record<string, unknown>,
      }));
    ctx.log(`Ainterviews [lenskart] India jobs: ${jobs.length}`);
    return jobs;
  },
};
