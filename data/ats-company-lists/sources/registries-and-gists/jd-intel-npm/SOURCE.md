# Source: jd-intel (npm package)

- **Registry page:** https://www.npmjs.com/package/jd-intel
- **Metadata:** https://registry.npmjs.org/jd-intel
- **Tarball (v0.7.0):** https://registry.npmjs.org/jd-intel/-/jd-intel-0.7.0.tgz
- **Repo:** https://github.com/prPMDev/jd-intel
- **Author:** Prashant R
- **ATS provider(s):** Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Recruitee, Teamtailor
- **Access method:** `curl` tarball from registry.npmjs.org -> `tar xzf` -> extracted `package/registry/*.json`
- **Date retrieved:** 2026-06-23
- **License:** MIT
- **Description:** A multi-ATS "registry" bundled inside the npm package. Each provider JSON is an
  array of objects `{slug, name, sector}` (Workday adds a `config` block with tenant/env/site).
  Curated, well-known companies — smaller than Feashliaa but richer (names + sectors + Workday config).

## Files & counts (all extracted from `package/registry/` inside the tarball)

| File | ATS | Count | Shape |
|------|-----|-------|-------|
| `greenhouse.json` | Greenhouse | 129 | `{slug,name,sector}` |
| `lever.json` | Lever | 30 | `{slug,name,sector}` |
| `ashby.json` | Ashby | 47 | `{slug,name,sector}` |
| `smartrecruiters.json` | SmartRecruiters | 28 | `{slug,name,sector}` |
| `recruitee.json` | Recruitee | 23 | `{slug,name,sector}` |
| `teamtailor.json` | Teamtailor | 31 | `{slug,name,sector}` |
| `workday.json` | Workday | 27 | `{slug,name,sector,config:{tenant,env,site}}` |

**Total: 315 entries across 7 ATS providers.**

## Notes
- Only source in this lane that covers SmartRecruiters, Recruitee, and Teamtailor with slugs.
- Workday `config` gives `{tenant, env, site}` (e.g. cisco -> wd5 / Cisco_Careers), same coordinates
  Feashliaa encodes as `slug|instance|site`.
