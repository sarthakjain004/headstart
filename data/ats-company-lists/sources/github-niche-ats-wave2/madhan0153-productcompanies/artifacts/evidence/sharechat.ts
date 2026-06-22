import type { CompanyConfig } from "./_types.js";
import type { RawJob } from "@prodmatch/shared";
import { fetchJson } from "../lib/http.js";
import { INDIA_RE, stripHtml } from "./_simple.js";

interface ShareChatJob {
  requisitionId: number;
  requisitionTitle?: string;
  designation?: string;
  orgUnitName?: string;
  officeLocationNames?: string[];
  employmentType?: string;
  jobDescription?: string | null;
  createdDate?: number;
  approvedDate?: number;
}

interface ShareChatGroup {
  title?: string;
  data?: ShareChatJob[];
}

interface ShareChatResponse {
  data?: { careersList?: ShareChatGroup[] };
}

function detailUrl(id: number): string {
  const payload = {
    pageType: "jd",
    cvSource: "careers",
    reqId: id,
    requester: { id: "", code: "", name: "" },
    page: "careers",
    bufilter: -1,
    customFields: {},
  };
  return `https://sharechat.mynexthire.com/employer/jobs?src=careers&p=${Buffer.from(JSON.stringify(payload)).toString("base64")}`;
}

export const sharechatConfig: CompanyConfig = {
  slug: "sharechat",
  async crawl(ctx): Promise<RawJob[]> {
    const data = await fetchJson<ShareChatResponse>("https://sharechat.com/api/careersList?limit=100", {
      headers: { Accept: "application/json", Referer: "https://sharechat.com/careers" },
      timeoutMs: 25_000,
    });
    const rows = (data.data?.careersList ?? []).flatMap((group) =>
      (group.data ?? []).map((job) => ({ ...job, groupTitle: group.title })),
    );
    const jobs = rows
      .filter((job) => INDIA_RE.test((job.officeLocationNames ?? []).join(" ")))
      .map((job): RawJob => ({
        external_id: String(job.requisitionId),
        title: job.requisitionTitle ?? job.designation ?? `ShareChat role ${job.requisitionId}`,
        location_raw: (job.officeLocationNames ?? []).join(" / "),
        description: stripHtml(job.jobDescription ?? ""),
        apply_url: detailUrl(job.requisitionId),
        posted_at: job.approvedDate ? new Date(job.approvedDate).toISOString() : undefined,
        raw: job as unknown as Record<string, unknown>,
      }));
    ctx.log(`ShareChat first-party API India jobs: ${jobs.length}`);
    return jobs;
  },
};
