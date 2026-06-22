import type { CompanyConfig, CrawlContext } from "./_types.js";
import type { RawJob } from "@prodmatch/shared";
import { fetchJson } from "../lib/http.js";

// Confirmed: Swiggy uses MyNextHire platform
// API: POST https://swiggy.mynexthire.com/employer/careers/reqlist/get
const API_URL = "https://swiggy.mynexthire.com/employer/careers/reqlist/get";

interface MnhJob {
  reqId: number;
  reqTitle: string;
  location: string;
  locationAddress?: string;
  buName?: string;
  jdDisplay?: string;
  expMin?: number;
  expMax?: number;
  approvedOn?: string;
  employmentType?: string;
}

interface MnhResponse {
  reqDetailsBOList?: MnhJob[];
}

export const swiggyConfig: CompanyConfig = {
  slug: "swiggy",
  async crawl({ log }: CrawlContext): Promise<RawJob[]> {
    const data = await fetchJson<MnhResponse>(API_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Referer: "https://careers.swiggy.com/",
        Origin: "https://careers.swiggy.com",
      },
      body: JSON.stringify({ source: "careers", code: "", filterByBuId: -1 }),
    });
    const jobs = data.reqDetailsBOList ?? [];
    log(`Total: ${jobs.length} jobs`);

    return jobs.map((j) => ({
      external_id: String(j.reqId),
      title: j.reqTitle,
      location_raw: j.locationAddress ?? j.location ?? "India",
      description: j.jdDisplay ?? "",
      apply_url: `https://careers.swiggy.com/#/careers?src=careers&req_id=${j.reqId}`,
      raw: j as unknown as Record<string, unknown>,
    }));
  },
};
