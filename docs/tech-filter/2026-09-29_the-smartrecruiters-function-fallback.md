# Tech filter version 7: the SmartRecruiters function fallback, and plural role vetoes

2026-09-29. `TECH_FILTER_VERSION` 6 → 7. Decision record: [ADR-0291](../adr/0291-only-the-it-function-stands-in-for-a-smartrecruiters-department.md). Issue: #570, options A and D.

All figures are on served table v298 (2026-09-29, 498,848 rows, read off HF with `lance`) and on live SmartRecruiters API reads made the same day, one request at a time with a pause between.

## Headline numbers

| measure | version 6 | version 7 |
|---|---:|---:|
| served rows kept (v298, 498,848) | 498,848 | 495,767 (−3,081 out) |
| of which SmartRecruiters (23,486) | 23,486 | 20,730 (−2,756) |
| postings entering on the 244 SmartRecruiters Boards walked (66,209 postings; a floor) | – | +377 |
| pre-detail gate passes, the 196 Boards with an "Engineering" rule-4 row | 16,012 | 11,131 |
| pre-detail gate passes, 60 random served SmartRecruiters Boards | 1,319 | 1,264 |
| blind hold-out recall / precision | 84.6% / 82.0% | 84.6% / 82.0% |
| labelled set: tech kept / non-tech kept | 344 of 345 / 84 of 540 | 344 of 345 / 84 of 540 |

The served table only holds what version 6 kept, so it shows what leaves but not what enters. What enters was measured on full live listings.

## Option A: which function stands in for a missing department

Population: the 4,536 served SmartRecruiters rows that are tech only through rule 4 (`tech-department`) under a department of "Engineering" (2,965) or "Information Technology" (1,571). These are the only two SmartRecruiters function labels `_TECH_DEPT` matches. `random.seed(570)` drew 300 ids; each was read from `GET /v1/companies/{co}/postings/{id}`. All 300 answered 200 with `active: true`. 15 stated a real department and are unaffected by any variant; the 285 with `department` null are below.

Two labellers read the titles, the second blind to the function, with the Board and its `industry` as context. They agreed on 263 of 285 (92%). T/N splits (3, all Information Technology) were settled as N: two crowdwork "AI Content Expert" postings (ADR-0087) and a course advert. A row either labeller marked ? is ?.

| function | T | ? | N |
|---|---:|---:|---:|
| Engineering (180) | 13 | 14 | 153 |
| Information Technology (105) | 63 | 16 | 26 |

By posting year (`releasedDate`):

| function | year | T | ? | N |
|---|---|---:|---:|---:|
| Engineering | 2023 or earlier | 0 | 0 | 4 |
| Engineering | 2024 | 2 | 1 | 1 |
| Engineering | 2025 | 1 | 3 | 10 |
| Engineering | 2026 | 10 | 10 | 138 |
| Information Technology | 2023 or earlier | 0 | 0 | 2 |
| Information Technology | 2024 | 13 | 1 | 0 |
| Information Technology | 2025 | 4 | 3 | 3 |
| Information Technology | 2026 | 46 | 12 | 21 |

<details><summary>The 285 labelled rows</summary>

| Board | posted | function | label A | label B | settled | title |
|---|---|---|---|---|---|---|
| `accorhotel` | 2026-07-30 | Engineering | N | N | N | Maintenance Technician II |
| `accorhotel` | 2026-09-09 | Engineering | N | N | N | Maintenance Technician |
| `accorhotel` | 2026-09-25 | Engineering | N | N | N | Portfolio Revenue Manager |
| `aecom2` | 2025-08-18 | Engineering | N | N | N | Site Inspector |
| `aecom2` | 2025-08-18 | Engineering | N | N | N | Construction Manager |
| `aecom2` | 2026-04-14 | Engineering | N | N | N | Associate Director |
| `aecom2` | 2026-05-20 | Engineering | N | N | N | Senior Analyst (Energy/Techno-economics/Decarbonisation) |
| `aecom2` | 2026-06-09 | Engineering | N | N | N | Transmission and Distribution Opportunities |
| `aecom2` | 2026-06-26 | Engineering | N | N | N | Principal Hydraulic Modeller - Flood Risk |
| `aecom2` | 2026-06-26 | Engineering | N | N | N | Principal Flood Modeller |
| `aecom2` | 2026-06-29 | Engineering | N | N | N | Senior Wastewater Modeller |
| `aecom2` | 2026-06-29 | Engineering | N | N | N | Senior Wastewater Modeller |
| `aecom2` | 2026-06-29 | Engineering | N | N | N | Hydraulic Specialist (Water) |
| `aecom2` | 2026-06-29 | Engineering | N | N | N | Project Manager - Environmental Remediation |
| `aecom2` | 2026-06-29 | Engineering | N | N | N | Project Executive-High Rise Commercial |
| `aecom2` | 2026-07-06 | Engineering | N | N | N | Construction Project Leader |
| `aecom2` | 2026-07-16 | Engineering | N | N | N | Data Center Construction Manager |
| `aecom2` | 2026-07-20 | Engineering | N | N | N | Drinking Water Treatment Manager |
| `aecom2` | 2026-07-29 | Engineering | ? | ? | ? | Digital Specialist - Environmental Data & Reporting Automation |
| `aecom2` | 2026-07-30 | Engineering | N | N | N | Discipline Lead - Traction Power |
| `aecom2` | 2026-08-04 | Engineering | N | N | N | Aviation Client & Programme Director |
| `aecom2` | 2026-08-06 | Engineering | N | N | N | Technical Director - Water Treatment |
| `aecom2` | 2026-08-06 | Engineering | N | N | N | Document Specialist |
| `aecom2` | 2026-08-10 | Engineering | N | N | N | Associate Director |
| `aecom2` | 2026-08-10 | Engineering | N | N | N | Hydropower Lead |
| `aecom2` | 2026-08-12 | Engineering | N | N | N | Bridge Inspection Department Manager |
| `aecom2` | 2026-08-21 | Engineering | N | N | N | Sr. Project Control Manager (Trivandrum, Mangalore & Ahmedabad) |
| `aecom2` | 2026-08-25 | Engineering | N | N | N | AECOM Water Resources Opportunities - ANZ |
| `aecom2` | 2026-08-25 | Engineering | N | N | N | AECOM Water Resources Opportunities - ANZ |
| `aecom2` | 2026-08-27 | Engineering | N | N | N | Technical Design Lead - Data Centres |
| `aecom2` | 2026-08-31 | Engineering | N | N | N | Transit Design Manager - Bus Rapid Transit |
| `aecom2` | 2026-09-04 | Engineering | N | N | N | Entry-Level Aviation Specialist - Hiring Event with AECOM - New York City |
| `aecom2` | 2026-09-06 | Engineering | N | N | N | Manager, Transmission & Distribution (Christchurch I Ōtautahi) |
| `aecom2` | 2026-09-14 | Engineering | N | N | N | Mid-Level Construction Inspector |
| `aecom2` | 2026-09-14 | Engineering | N | N | N | Construction Intern – AECOM Hunt |
| `aecom2` | 2026-09-17 | Engineering | N | N | N | Graduate Rail Project Manager - Swindon (Immediate Start or 2026 Start) |
| `aecom2` | 2026-09-17 | Engineering | N | N | N | Senior Construction Project Manager (Courthouses) |
| `aecom2` | 2026-09-18 | Engineering | N | N | N | Design Manager - Transportation |
| `aecom2` | 2026-09-21 | Engineering | N | N | N | Graduate Flood Risk Consultant / Manchester |
| `aecom2` | 2026-09-22 | Engineering | N | N | N | Senior Power System Studies I NSW |
| `aecom2` | 2026-09-22 | Engineering | N | N | N | Design Manager, Airport |
| `aecom2` | 2026-09-24 | Engineering | N | N | N | Senior Water Conveyance Project Manager |
| `aecom2` | 2026-09-25 | Engineering | N | N | N | Transit Market Sector Leader |
| `AmarkTalentSolutions1` | 2026-05-21 | Engineering | N | N | N | Construction Project Manager |
| `applusidiada1` | 2026-02-10 | Engineering | N | N | N | Work with us (Brazil) |
| `assystem` | 2026-02-24 | Engineering | N | N | N | DGM/General Manager – Ports |
| `assystem` | 2026-03-30 | Engineering | N | N | N | Senior manager |
| `assystem` | 2026-04-27 | Engineering | N | N | N | Framework Manager |
| `assystem` | 2026-07-14 | Engineering | N | N | N | Quantity Surveyor |
| `assystem` | 2026-09-21 | Engineering | N | N | N | Architect |
| `assystem` | 2026-09-28 | Engineering | ? | ? | ? | Business Architect |
| `aumovio` | 2026-05-22 | Engineering | N | N | N | Maintenance Technician |
| `aumovio` | 2026-06-16 | Engineering | N | N | N | Branding & Event Specialist Intern [IDA: 00037] |
| `aumovio` | 2026-08-12 | Engineering | ? | ? | ? | UX_Customer Project Lead (CPL) |
| `boschgroup` | 2024-11-29 | Engineering | T | T | T | 自动驾驶多模态感知算法专家_XC |
| `boschgroup` | 2024-12-19 | Engineering | T | T | T | AI Planning Expert_XC |
| `boschgroup` | 2024-12-24 | Engineering | N | N | N | Sr. HW Architect _XC |
| `boschgroup` | 2025-01-14 | Engineering | T | T | T | SW/AI Safety Expert_XC |
| `boschgroup` | 2025-03-11 | Engineering | N | N | N | EBS机械设计工程师_PS |
| `boschgroup` | 2025-08-05 | Engineering | N | N | N | Product Planner_ME |
| `boschgroup` | 2025-10-30 | Engineering | N | N | N | Internship 2026, Labolatory Quality Management, PowerSolutions (PS/QMM2-HmjP) |
| `boschgroup` | 2025-11-23 | Engineering | ? | T | ? | 资深产品团队经理(智能驾驶)_XC |
| `boschgroup` | 2026-01-19 | Engineering | N | N | N | Internship 2026, Quality Management (AmaP/QMM1_VSQ) |
| `boschgroup` | 2026-01-23 | Engineering | N | N | N | Internship 2026, Product Quality Management, Power Solutions (PS/QMM1.1-HmjP) |
| `boschgroup` | 2026-02-28 | Engineering | T | T | T | 嵌入式软件开发工程师 (AI方向)_SO |
| `boschgroup` | 2026-05-04 | Engineering | N | N | N | Export Control Lead Expert |
| `boschgroup` | 2026-05-11 | Engineering | N | N | N | Testing process technician 测试制程技术员_ME |
| `boschgroup` | 2026-05-13 | Engineering | ? | ? | ? | Product Testing / Industrialization Expert_XC |
| `boschgroup` | 2026-05-14 | Engineering | N | N | N | 电子硬件工程师 (制动系统ECU)_SO |
| `boschgroup` | 2026-05-20 | Engineering | N | N | N | Service Technician |
| `boschgroup` | 2026-05-25 | Engineering | N | N | N | Customer Project Manager/客户项目经理_ITK |
| `boschgroup` | 2026-06-26 | Engineering | N | N | N | Industrialization Project Manager （IPM） |
| `boschgroup` | 2026-07-03 | Engineering | N | N | N | Project Manager_PT |
| `boschgroup` | 2026-07-13 | Engineering | N | N | N | Purchasing Manager |
| `boschgroup` | 2026-07-16 | Engineering | N | N | N | Build Your Future with Bosch – Join Our Talent Pool \| Bosch HcP & R&D |
| `boschgroup` | 2026-07-22 | Engineering | T | T | T | ADAS-HiL Expert |
| `boschgroup` | 2026-07-22 | Engineering | T | T | T | Quality Assurance (QA) - Lead - MiDAS |
| `boschgroup` | 2026-07-24 | Engineering | N | N | N | 传感器电子工程师_VM |
| `boschgroup` | 2026-08-13 | Engineering | N | N | N | Startup Venture Expert CN_CR |
| `boschgroup` | 2026-08-26 | Engineering | N | N | N | 高级硬件开发工程师_SO |
| `boschgroup` | 2026-09-02 | Engineering | N | N | N | Project Manager / PMO(eDrive)_ EM |
| `boschgroup` | 2026-09-21 | Engineering | ? | T | ? | MAM/DAM Product Operations Specialist (f/m/div.) |
| `boschgroup` | 2026-09-28 | Engineering | N | N | N | Project Manager BEG |
| `CallNicholasInc` | 2026-09-08 | Engineering | N | N | N | Technician |
| `CheckPointSoftwareTechnologies2` | 2026-08-07 | Engineering | T | T | T | Professional Services Consultant, Brisbane |
| `CheckPointSoftwareTechnologies2` | 2026-09-17 | Engineering | N | N | N | Assistant Controller |
| `cima2` | 2026-09-10 | Engineering | N | N | N | Strategic Lead – Indigenous Relations & Federal Opportunities (Intermediate to Senior) |
| `cima2` | 2026-09-10 | Engineering | N | N | N | Construction Inspector– Infrastructure Field Services |
| `cima2` | 2026-09-17 | Engineering | N | N | N | Telecom Drafter |
| `cima2` | 2026-09-23 | Engineering | N | N | N | Transmission Lines Designer |
| `cima2` | 2026-09-23 | Engineering | N | N | N | Transmission Lines Designer |
| `cima2` | 2026-09-23 | Engineering | N | N | N | Transmission Lines Designer |
| `cima2` | 2026-09-23 | Engineering | N | N | N | Transmission Lines Designer |
| `cityofnewyork` | 2026-08-13 | Engineering | N | N | N | Construction Project Manager |
| `cityofnewyork` | 2026-08-18 | Engineering | N | N | N | ACCOUNTABLE MANAGER |
| `cityofnewyork` | 2026-08-18 | Engineering | N | N | N | ACCOUNTABLE MANAGER |
| `cityofnewyork` | 2026-08-20 | Engineering | N | N | N | Project Manager |
| `cityofnewyork` | 2026-08-22 | Engineering | N | N | N | Project Manager |
| `cityofnewyork` | 2026-08-26 | Engineering | N | N | N | Senior Project Coordinator, OLS |
| `cityofnewyork` | 2026-08-26 | Engineering | N | N | N | Project Coordinator |
| `cityofnewyork` | 2026-08-27 | Engineering | N | N | N | Deputy Director |
| `cityofnewyork` | 2026-09-02 | Engineering | N | N | N | Project Manager |
| `cityofnewyork` | 2026-09-02 | Engineering | N | N | N | Project Manager |
| `cityofnewyork` | 2026-09-02 | Engineering | N | N | N | Project Manager |
| `cityofnewyork` | 2026-09-05 | Engineering | N | N | N | ASSISTANT PROJECT MANAGER |
| `cityofnewyork` | 2026-09-09 | Engineering | N | N | N | ACCOUNTABLE MANAGER |
| `cityofnewyork` | 2026-09-09 | Engineering | N | N | N | SCHEDULE CONTROLS MANAGER |
| `cityofnewyork` | 2026-09-15 | Engineering | N | N | N | Assistant Chief Plan Examiner, Sidewalk Shed and Construction Progress Monitoring |
| `cityofnewyork` | 2026-09-16 | Engineering | N | N | N | Accessibility Specialist |
| `cityofnewyork` | 2026-09-17 | Engineering | N | N | N | Chief of Staff, Division of Building and Land Development Service |
| `cityofnewyork` | 2026-09-22 | Engineering | N | N | N | Project Reviewer |
| `continental` | 2026-07-31 | Engineering | N | N | N | Group Leader Process - Tires |
| `continentalgroupsectorcontitech` | 2026-09-14 | Engineering | N | N | N | Customer Quality & Complaints Specialist |
| `continentalgroupsectorcontitech` | 2026-09-14 | Engineering | N | N | N | Regional Purchaser |
| `continentalgroupsectorcontitech` | 2026-09-18 | Engineering | N | N | N | Supply Chain & Operations Student Co-Op – Year-Round (Halstead, KS) |
| `crowninnovationsinc` | 2026-04-16 | Engineering | N | N | N | Electronics Technician - Install/Dispo - CSA |
| `crowninnovationsinc` | 2026-05-07 | Engineering | N | N | N | Automation Site Preparation Installation Technician |
| `crowninnovationsinc` | 2026-08-29 | Engineering | N | N | N | Communication Electronics Installation Technician |
| `DellTree` | 2025-01-16 | Engineering | N | N | N | Operator technician |
| `DigitalToolDieInc` | 2026-04-17 | Engineering | N | N | N | Die Designer |
| `dreessommerse` | 2026-07-21 | Engineering | N | N | N | AV Designer (f/m/d) |
| `eatngolimited` | 2025-04-28 | Engineering | N | N | N | MAINTENANCE TECHNICIAN |
| `egisgroup` | 2026-01-08 | Engineering | N | N | N | Project Manager ID - Site |
| `egisgroup` | 2026-04-03 | Engineering | N | N | N | Operation Manager - Infrastructure Maintenance |
| `egisgroup` | 2026-06-03 | Engineering | N | N | N | Senior CAD Technician - Highways |
| `egisgroup` | 2026-06-23 | Engineering | N | N | N | Project Manager Road & Rail Design |
| `egisgroup` | 2026-07-05 | Engineering | N | N | N | HSE Inspector |
| `egisgroup` | 2026-07-13 | Engineering | N | N | N | Document Controller |
| `egisgroup` | 2026-07-17 | Engineering | N | N | N | BIM Modeler – Utilities |
| `egisgroup` | 2026-08-31 | Engineering | N | N | N | Director of Road O&M M/F |
| `egisgroup` | 2026-09-16 | Engineering | N | N | N | Claims Manager |
| `egisgroup` | 2026-09-17 | Engineering | N | N | N | Document Controller |
| `egisgroup` | 2026-09-18 | Engineering | N | ? | ? | Requirements Management Lead |
| `egisgroup` | 2026-09-21 | Engineering | N | N | N | Project Manager, Communications |
| `gdmsi` | 2026-08-11 | Engineering | ? | ? | ? | System Integration, Verification & Validation |
| `gdmsi` | 2026-09-14 | Engineering | ? | ? | ? | Co-op Winter 2027 - Systems Integration, Verification, and Validation - 8-12 months |
| `hackerrank` | 2016-03-11 | Engineering | N | N | N | Test |
| `intuitive` | 2026-02-25 | Engineering | T | T | T | Senior Product Manager - Downstream Software |
| `intuitive` | 2026-09-03 | Engineering | T | T | T | Staff Interaction Designer |
| `jobsforhumanity` | 2025-03-24 | Engineering | N | N | N | Electronic Specialist – Lighting Industry |
| `jobsforhumanity` | 2025-05-26 | Engineering | N | N | N | Senior Project Manager |
| `jobsforhumanity` | 2026-02-24 | Engineering | N | N | N | Maintenance Technician |
| `jobsforhumanity` | 2026-04-30 | Engineering | N | N | N | CAD Technician |
| `jobsforhumanity` | 2026-05-17 | Engineering | N | N | N | Procurement Manager (MEP & Fitout Construction) |
| `jobsforhumanity` | 2026-06-29 | Engineering | N | N | N | Senior Project Manager / Project Manager (Aramco Exp Must) |
| `jobsforhumanity` | 2026-08-05 | Engineering | N | N | N | Contract Support UWE |
| `jobsforhumanity` | 2026-08-05 | Engineering | N | N | N | Contract Support Retail |
| `kanadeviainova` | 2026-09-10 | Engineering | N | N | N | WtX Site Supervisor |
| `konecranes` | 2026-07-13 | Engineering | N | N | N | Service Technician |
| `konecranes` | 2026-08-24 | Engineering | N | N | N | DET - Technician Crane Service |
| `lely1` | 2026-09-17 | Engineering | N | N | N | Technical Service Specialist |
| `lely1` | 2026-09-23 | Engineering | N | N | N | Teamlead Project Manager Product Development |
| `MackenzieSearchGroup` | 2014-03-06 | Engineering | N | N | N | Senior Maintenance Technician |
| `maglevaeroinc` | 2022-01-18 | Engineering | N | N | N | Create your own job! |
| `McArthurGlenUKLtd1` | 2026-09-25 | Engineering | N | N | N | Facilities Manager |
| `meta1` | 2026-09-18 | Engineering | N | N | N | Electromechanical Technician |
| `nbcuniversal3` | 2026-09-21 | Engineering | ? | N | ? | Fiber Optics Associate Manager |
| `OnPoint1` | 2026-07-17 | Engineering | N | N | N | Landscape Architect |
| `ramboll3` | 2025-10-23 | Engineering | N | N | N | Ramboll is growing its Rail Signalling & Telecom team! |
| `ramboll3` | 2026-07-24 | Engineering | N | N | N | HV Critical Systems Director |
| `ramboll3` | 2026-08-04 | Engineering | N | N | N | Construction Project Manager |
| `ramboll3` | 2026-08-06 | Engineering | N | N | N | Senior Data Center Consultant |
| `ramboll3` | 2026-08-06 | Engineering | N | N | N | Senior Data Center Consultant |
| `ramboll3` | 2026-08-18 | Engineering | N | N | N | Renewable Energy/BESS Specialist |
| `renesaselectronics` | 2026-09-09 | Engineering | N | N | N | Staff Packaging Design Engineer封装开发工程师 （功率模块） |
| `renesaselectronics` | 2026-09-22 | Engineering | N | N | N | Product Marketing Manager, High Performance AI and Compute Power |
| `renesaselectronics` | 2026-09-28 | Engineering | T | T | T | Software Manager |
| `renesaselectronics` | 2026-09-28 | Engineering | N | N | N | Senior Staff Product Marketing Specialist |
| `sikaag` | 2026-07-21 | Engineering | N | N | N | EHS Manager, Korea |
| `streemenergy` | 2020-10-22 | Engineering | N | N | N | Spontaneous Application |
| `t-systemsictindiapvtltd1` | 2025-07-22 | Engineering | ? | ? | ? | Process Manager |
| `t-systemsictindiapvtltd1` | 2026-09-11 | Engineering | T | T | T | Product Owner |
| `ThorntonEngineering` | 2026-06-14 | Engineering | N | N | N | Boilermaker |
| `TycoonsProdCorporation` | 2026-04-30 | Engineering | T | T | T | Senior Security Enginneer |
| `utac` | 2026-09-11 | Engineering | N | N | N | Group ESG Project Co-ordinator (Maternity Cover - 6 months FTC) |
| `ValidationEngineeringGroup` | 2025-09-17 | Engineering | ? | T | ? | Computer Systems Validation Specialist |
| `versant3` | 2026-09-25 | Engineering | T | T | T | Sr. Data Modeler |
| `westerndigital` | 2026-09-14 | Engineering | ? | T | ? | Spring 2027 Co-Op - AI Systems Strategy |
| `WilliamsRacing` | 2026-05-20 | Engineering | N | N | N | Indirect Procurement Business Partner |
| `XcelAgencyInc` | 2024-12-12 | Engineering | ? | T | ? | Eloqua Campaign Consultant |
| `AcmeCorp091614` | 2018-05-17 | Information Technology | N | N | N | Fox adorer |
| `AcmeCorp091614` | 2022-01-25 | Information Technology | N | N | N | test |
| `adgagroupconsultantsinc1` | 2026-08-04 | Information Technology | N | N | N | Physical Security Specialist |
| `aecom2` | 2026-05-22 | Information Technology | ? | N | ? | Senior Information Manager |
| `aecom2` | 2026-06-16 | Information Technology | N | N | N | Senior Revit Technician |
| `aecom2` | 2026-06-16 | Information Technology | N | N | N | Senior CAD Technician |
| `aecom2` | 2026-07-14 | Information Technology | N | N | N | BIM Coordinator |
| `aecom2` | 2026-07-28 | Information Technology | ? | N | ? | Technician - Digital Delivery |
| `aecom2` | 2026-08-26 | Information Technology | N | N | N | BIM Manager |
| `aecom2` | 2026-08-31 | Information Technology | T | T | T | Trimble Unity Maintain/Cityworks Administrator (Airports) |
| `aecom2` | 2026-08-31 | Information Technology | T | T | T | Trimble Unity Maintain/Cityworks Administrator (Airports) |
| `aecom2` | 2026-09-10 | Information Technology | N | N | N | BIM Intern - Hiring Event with AECOM - Philadelphia |
| `aecom2` | 2026-09-25 | Information Technology | N | N | N | Senior Designer - BIM Substation |
| `aecom2` | 2026-09-25 | Information Technology | N | N | N | BIM/ VDC Intern – AECOM Hunt |
| `AETOS` | 2025-04-16 | Information Technology | T | T | T | Microsoft PowerApps Administrator |
| `antonpaar1` | 2026-09-17 | Information Technology | N | N | N | Quality Technician (m/f/x) – Quality Assurance & Complaint Management |
| `AppXite` | 2026-09-02 | Information Technology | ? | T | ? | Cloud Alliance Manager – AWS |
| `bctechpro` | 2024-01-24 | Information Technology | T | T | T | Computer Field Technician |
| `bctechpro` | 2024-01-30 | Information Technology | T | T | T | Computer Field Technician |
| `bctechpro` | 2024-05-30 | Information Technology | T | T | T | Computer Field Technician |
| `bctechpro` | 2024-06-18 | Information Technology | T | T | T | Computer Field Technician |
| `bctechpro` | 2024-07-23 | Information Technology | T | T | T | Computer Field Technician |
| `bctechpro` | 2024-08-15 | Information Technology | T | T | T | Computer Field Technician |
| `bctechpro` | 2024-08-21 | Information Technology | T | T | T | Computer Field Technician |
| `bctechpro` | 2024-09-25 | Information Technology | T | T | T | Computer Field Technician |
| `bctechpro` | 2024-12-16 | Information Technology | T | T | T | Computer Field Technician |
| `bctechpro` | 2025-01-22 | Information Technology | T | T | T | Computer Field Technician |
| `bctechpro` | 2026-04-08 | Information Technology | T | T | T | Computer Field Technician |
| `bctechpro` | 2026-04-13 | Information Technology | T | T | T | Computer Field Technician |
| `bctechpro` | 2026-05-04 | Information Technology | T | T | T | Computer Field Technician |
| `bctechpro` | 2026-05-18 | Information Technology | T | T | T | Computer Field Technician |
| `bctechpro` | 2026-06-09 | Information Technology | T | T | T | Computer Field Technician |
| `bctechpro` | 2026-06-11 | Information Technology | T | T | T | Computer Field Technician |
| `bctechpro` | 2026-06-11 | Information Technology | T | T | T | Computer Field Technician |
| `bctechpro` | 2026-06-11 | Information Technology | T | T | T | Computer Field Technician |
| `bctechpro` | 2026-07-06 | Information Technology | T | T | T | Computer Field Technician |
| `bctechpro` | 2026-07-06 | Information Technology | T | T | T | Computer Field Technician |
| `bctechpro` | 2026-07-06 | Information Technology | T | T | T | Computer Field Technician |
| `bctechpro` | 2026-07-06 | Information Technology | T | T | T | Computer Field Technician |
| `bctechpro` | 2026-07-13 | Information Technology | T | T | T | Computer Field Technician |
| `bctechpro` | 2026-07-31 | Information Technology | T | T | T | Computer Field Technician |
| `bctechpro` | 2026-08-17 | Information Technology | T | T | T | Computer Field Technician |
| `bctechpro` | 2026-09-23 | Information Technology | T | T | T | Computer Field Technician |
| `Bertelsmann-Jobs` | 2026-08-11 | Information Technology | T | T | T | SAP Authorizations and User Management Consultant |
| `Bertelsmann-Jobs` | 2026-09-14 | Information Technology | N | N | N | Consultant Pharmacovigilance (PV) |
| `Bertelsmann-Jobs` | 2026-09-16 | Information Technology | T | T | T | Master Thesis Student (m/f/d) – Explainable AI for Fraud Detection |
| `boschgroup` | 2026-07-08 | Information Technology | T | T | T | SAP Intralogistics To Demand Consultant |
| `boschgroup` | 2026-07-23 | Information Technology | T | T | T | Team Lead – Hyper Automation (RPA, AI, ML) |
| `boschgroup` | 2026-09-07 | Information Technology | T | T | T | Senior Stibo STEP Platform Specialist (f/m) |
| `boschgroup` | 2026-09-11 | Information Technology | ? | T | ? | 数字化部门负责人BDO Department Head_EM |
| `boschgroup` | 2026-09-21 | Information Technology | T | T | T | Data Analytics Expert (f/m/div.) |
| `cieloprojects` | 2024-10-15 | Information Technology | T | T | T | Integrations Architect Lead |
| `cima2` | 2026-08-20 | Information Technology | N | N | N | Senior Proposal Advisor |
| `cityofnewyork` | 2026-08-28 | Information Technology | ? | N | ? | Technical Coordinator, Assigned Counsel Plan |
| `cityofnewyork` | 2026-09-02 | Information Technology | ? | N | ? | Policy Analyst for Technology & Innovation |
| `cityofnewyork` | 2026-09-03 | Information Technology | N | N | N | Manager, Public Safety Communications Facilities |
| `cityofnewyork` | 2026-09-09 | Information Technology | ? | ? | ? | Data & Project Management Analyst |
| `cityofnewyork` | 2026-09-24 | Information Technology | N | N | N | SUPERVISOR STOCK WORKER |
| `cityofnewyork` | 2026-09-25 | Information Technology | T | T | T | Digital Forensic Lab Analyst (DFL Analyst) |
| `cliffordchance` | 2026-05-06 | Information Technology | ? | T | ? | Legal Technology Advisor |
| `continental` | 2026-09-14 | Information Technology | T | T | T | Senior Regional IT Locations Coordinator - Tires |
| `EI-TechnologiesMena` | 2026-07-22 | Information Technology | T | T | T | Salesforce Senior Technico-Functional Consultant |
| `hrconnectlimited` | 2026-09-15 | Information Technology | N | N | N | Component Assembly Technician |
| `inetum2` | 2026-09-28 | Information Technology | T | T | T | Business Analyst |
| `ITManagementCorpDba101VOICE` | 2026-09-24 | Information Technology | ? | N | ? | Network Cabling & Low Voltage Technician |
| `jobsforhumanity` | 2024-12-11 | Information Technology | T | T | T | Project Manager Technology (for Immediate Joiners) |
| `jobsforhumanity` | 2024-12-13 | Information Technology | T | T | T | Project Manager Technology (for Immediate Joiners) |
| `jobsforhumanity` | 2025-08-25 | Information Technology | T | T | T | Analytics Consultant |
| `jobsforhumanity` | 2025-09-29 | Information Technology | N | T | N | AI Content Expert, Artificial General Intelligence |
| `jobsforhumanity` | 2025-09-29 | Information Technology | N | T | N | AI Content Expert, Artificial General Intelligence |
| `jobsforhumanity` | 2025-12-13 | Information Technology | ? | T | ? | AWS I2PA Pre-Apprenticeship - Bristol, PA |
| `jobsforhumanity` | 2026-05-14 | Information Technology | N | N | N | On-site Audio and Video Collection |
| `jobsforhumanity` | 2026-05-20 | Information Technology | N | N | N | Paid On-site Study in Hawaii |
| `jysk` | 2026-09-14 | Information Technology | T | T | T | Security Specialist, Vulnerability & Configuration Management |
| `KPMGNederland` | 2026-09-14 | Information Technology | T | T | T | Senior Manager - Technology Transformation |
| `Learnkwikcom` | 2025-07-22 | Information Technology | N | T | N | Cyber Security Training |
| `LogicalParadigm1` | 2026-06-05 | Information Technology | T | T | T | Associate Business Analyst |
| `louisdreyfuscompany` | 2026-09-24 | Information Technology | ? | N | ? | Solution Manager Procurement |
| `msxinternational` | 2026-08-19 | Information Technology | N | N | N | Automotive Technical Trainer |
| `natixisinportugal` | 2026-09-09 | Information Technology | T | T | T | Team Leader \| IT CIB |
| `natixisinportugal` | 2026-09-23 | Information Technology | T | T | T | Business  Analyst \| DSI Corporate |
| `necsws` | 2026-09-07 | Information Technology | T | T | T | Product Manager - NEC Housing |
| `netcompany1` | 2026-06-18 | Information Technology | N | N | N | Unsolicited Application |
| `netcompany1` | 2026-09-09 | Information Technology | T | T | T | Lead Product Manager |
| `Projekt0708GmbH` | 2026-07-02 | Information Technology | T | T | T | HR/IT-Project Manager SAP HCM |
| `QantasGroup` | 2026-09-15 | Information Technology | T | T | T | Product Designer |
| `redicasystems` | 2026-03-04 | Information Technology | ? | T | ? | Data Solutions Coordinator (Regulatory Intelligence) |
| `RostanTechnoloiges` | 2025-01-10 | Information Technology | T | T | T | Fusion Finance Functional Consultant |
| `sbtglobalinc` | 2026-09-17 | Information Technology | T | T | T | Jr. Security Admin (Korean Bilingual) |
| `SierreTechnologies` | 2024-11-27 | Information Technology | T | T | T | Business Analyst with Pharma background |
| `sosi1` | 2026-09-28 | Information Technology | ? | ? | ? | Watch Officer Lead |
| `STARWorkforce1` | 2024-10-14 | Information Technology | ? | ? | ? | Retail QA SME |
| `sutherland` | 2026-09-01 | Information Technology | T | T | T | Project Manager - Software Solutions Implementation |
| `sutherland` | 2026-09-17 | Information Technology | T | T | T | Business Analyst - Insurance Domain |
| `TEKWISSENLLC` | 2026-02-27 | Information Technology | T | T | T | IMAC Deployment Technician |
| `tieto2` | 2026-04-29 | Information Technology | T | T | T | Senior SAP Project Manager (m/f/d) - Tieto Tech Consulting |
| `tieto2` | 2026-05-06 | Information Technology | T | T | T | Product Owner Data, Analytics & AI - Tieto Indtech  (m/f/d) |
| `tieto2` | 2026-09-03 | Information Technology | N | N | N | Junior Invoice specialist - Tieto Tech Consulting (m/f/d) |
| `tieto2` | 2026-09-09 | Information Technology | N | N | N | Head of Operational Excellence, Tieto Indtech |
| `tieto2` | 2026-09-17 | Information Technology | N | N | N | Bid manager - Tieto Caretech (m/f/d) |
| `tieto2` | 2026-09-24 | Information Technology | T | T | T | Release Train Architect - Tieto Caretech (m/f/d) |
| `timmonsgroup1` | 2026-08-19 | Information Technology | T | T | T | AI Innovation and Enablement Leader |
| `VeteranTechnologyLeadersLLC` | 2025-02-26 | Information Technology | ? | T | ? | Business Process Consultant (Senior-level) |
| `vichara` | 2026-05-06 | Information Technology | T | T | T | VP - Risk Technology |
| `VirtualEnterpriseArchitects` | 2025-01-31 | Information Technology | ? | ? | ? | Business Architect |

</details>

### What leaves under variant (ii)

Every served row on the 196 Boards carrying an "Engineering" rule-4 row was joined to its Board's full live listing (57,411 postings). 2,755 rows leave, on 170 Boards. 22 served rows on those Boards were no longer listed and are not counted; they leave anyway as ordinary closures. Top Boards:

| Board | rows |
|---|---:|
| `smartrecruiters:aecom2` | 651 |
| `smartrecruiters:boschgroup` | 488 |
| `smartrecruiters:cityofnewyork` | 232 |
| `smartrecruiters:egisgroup` | 174 |
| `smartrecruiters:jobsforhumanity` | 129 |
| `smartrecruiters:ramboll3` | 85 |
| `smartrecruiters:cima2` | 77 |
| `smartrecruiters:assystem` | 66 |
| `smartrecruiters:accorhotel` | 61 |
| `smartrecruiters:CheckPointSoftwareTechnologies2` | 43 |
| `smartrecruiters:konecranes` | 41 |
| `smartrecruiters:aumovio` | 40 |
| `smartrecruiters:intuitive` | 37 |
| `smartrecruiters:renesaselectronics` | 30 |
| `smartrecruiters:t-systemsictindiapvtltd1` | 27 |
| `smartrecruiters:Ramboll2` | 21 |
| `smartrecruiters:abbvie` | 20 |
| `smartrecruiters:WilliamsRacing` | 20 |
| `smartrecruiters:continental` | 17 |
| `smartrecruiters:crowninnovationsinc` | 16 |

### What enters under variant (ii)

A null department no longer carries SmartRecruiters' Sales or Manufacturing function, whose labels rule 2 reads as a veto on a generic "…Engineer" (`_NON_SOFTWARE`). Over the 196 Boards above plus a separate random draw of 60 served SmartRecruiters Boards (`random.seed(5700)`, 10,280 postings), 377 postings not in today's Tech subset would enter: 355 under Manufacturing and 22 under Sales. By Board: `renesaselectronics` 207, `boschgroup` 90, `intuitive` 26, `sikaag` 8, `tomra` 7, the rest 39.

About 40 are software, EDA or digital-design work that today's veto drops, for example "WPF Developer" (3), "IT Solution Developer (Semiconductor)", "Internship in IT Solution Developer", "Principal Linux Driver Engineer", "Senior SCCM/MECM Engineer", "AI Development Engineer_ME", "Sr EDA Engineer", "Principal Engineer, STA & Synthesis", and seven digital or AMS verification engineers. Most of the rest are process, product, quality, supplier and field-application engineers: "Process Engineer" (10), "Staff Process Engineer" (6), "Field Service Engineer" (6). That is the recall-biased rule 3 applied as on any ATS that states no department, and it is what SmartRecruiters did before #564.

That read counted titles; it did not label them. A random 60 of the 377 (`random.seed(858)`) were then labelled one by one on the same scale as the Option A sample, with each posting's `releasedDate`:

| label | postings | posted 2026 | 2025 | 2024 |
|---|---:|---:|---:|---:|
| tech | 3 | 3 | 0 | 0 |
| ? | 15 | 15 | 0 | 0 |
| not tech | 42 | 39 | 2 | 1 |

Scaled to 377, that is about 20 tech, 95 borderline and 265 non-tech postings entering. The three tech rows are Renesas design-enablement (EDA flow) and IC-architect roles. The borderline ones are hardware validation, analog and mixed-signal design, product test, semiconductor QA and MCU application engineers. 59 of the 60 are under the Manufacturing function.

The 377 is a floor. It counts only the 244 Boards walked, and 196 of those were chosen because they carry an "Engineering" row. The unbiased random walk found 1 gain on 60 served Boards (10,280 postings), so the other roughly 800 of the 1,051 SmartRecruiters Boards on v298 would add about 15. SmartRecruiters Boards with no served row were not walked, and their gains are unmeasured.

<details><summary>The 60 labelled gains</summary>

| Board | posted | function | label | title |
|---|---|---|---|---|
| `boschgroup` | 2024-11-26 | Manufacturing | N | PQA PCB Engineer |
| `boschgroup` | 2025-06-21 | Manufacturing | N | Failure Analysis Engineer_ME |
| `boschgroup` | 2026-02-13 | Manufacturing | ? | Test Development Engineer |
| `boschgroup` | 2026-03-03 | Manufacturing | N | IN_MIVIN_ Sr Engineer - Purchasing Template _IN |
| `boschgroup` | 2026-03-30 | Manufacturing | N | Customer Quality Engineer_DCCC |
| `boschgroup` | 2026-04-10 | Manufacturing | N | Jr. Packaging Engineer |
| `boschgroup` | 2026-04-16 | Manufacturing | N | Maintenance Group Leader / Staff Maintenance Engineer |
| `boschgroup` | 2026-04-23 | Manufacturing | N | Maintenance Engineer (Final Assembly) |
| `boschgroup` | 2026-05-13 | Manufacturing | N | Senior Test Maintenance Engineer |
| `boschgroup` | 2026-06-11 | Manufacturing | N | Process Engineer_ME |
| `boschgroup` | 2026-08-13 | Manufacturing | N | Technical Engineering Function (TEF) Winter Intern |
| `boschgroup` | 2026-08-14 | Manufacturing | ? | Robot Operations & Reliability Engineer_PS |
| `boschgroup` | 2026-08-20 | Manufacturing | N | Process and Equipment Engineer |
| `boschgroup` | 2026-08-20 | Manufacturing | N | Process and Equipment Engineer (Acceleration Sensors) |
| `boschgroup` | 2026-08-27 | Manufacturing | N | Quality Engineer |
| `boschgroup` | 2026-09-02 | Manufacturing | ? | ITM Engineer_PS |
| `boschgroup` | 2026-09-15 | Manufacturing | N | 电机生产测试工程师Testing Engineer_EM |
| `boschgroup` | 2026-09-22 | Manufacturing | N | Gyártósori minőségügyi mérnök I Line Quality Engineer |
| `cieloprojects` | 2025-12-02 | Manufacturing | N | Rolls-Royce - Process Safety Management Engineer |
| `intuitive` | 2026-06-30 | Manufacturing | N | Staff Quality Engineer - New Product Development |
| `intuitive` | 2026-08-25 | Manufacturing | N | Sr. Engineer - Value Engineering |
| `intuitive` | 2026-09-22 | Manufacturing | N | Manufaturing Engineer 3 |
| `renesaselectronics` | 2026-03-10 | Manufacturing | T | Sr Engineer, Design Enablement |
| `renesaselectronics` | 2026-04-06 | Manufacturing | T | Sr Engineer, Design Enablement |
| `renesaselectronics` | 2026-04-14 | Manufacturing | ? | Senior HW Validation Engineer |
| `renesaselectronics` | 2026-05-05 | Manufacturing | ? | Senior Engineer, Analog Layout |
| `renesaselectronics` | 2026-05-12 | Manufacturing | ? | Staff Engineer / Sr Engineer / Engineer, Validation |
| `renesaselectronics` | 2026-05-26 | Manufacturing | N | Staff Product Engineer |
| `renesaselectronics` | 2026-06-04 | Manufacturing | ? | Sr Quality Assurance Engineer |
| `renesaselectronics` | 2026-07-06 | Manufacturing | N | Assembly Engineer (Senior/Staff) |
| `renesaselectronics` | 2026-07-30 | Manufacturing | N | Principal Medium Voltage (MV) Power MOSFET Process Integration Engineer |
| `renesaselectronics` | 2026-08-18 | Manufacturing | ? | Principal Quality Assurance Engineer |
| `renesaselectronics` | 2026-08-25 | Manufacturing | ? | Intern - Analog Design Engineer |
| `renesaselectronics` | 2026-08-31 | Manufacturing | ? | Staff Validation Engineer |
| `renesaselectronics` | 2026-09-01 | Manufacturing | N | Principal Engineer (f/m/d) Application Engineering |
| `renesaselectronics` | 2026-09-01 | Manufacturing | N | Senior NPI Engineer |
| `renesaselectronics` | 2026-09-04 | Manufacturing | ? | Staff MCU Application Engineer |
| `renesaselectronics` | 2026-09-07 | Manufacturing | N | Staff Application Engineer |
| `renesaselectronics` | 2026-09-14 | Manufacturing | N | Manager, Semiconductor IC Packaging & Assembly Engineering |
| `renesaselectronics` | 2026-09-14 | Manufacturing | N | Process Engineer |
| `renesaselectronics` | 2026-09-14 | Manufacturing | N | Process Engineer |
| `renesaselectronics` | 2026-09-14 | Manufacturing | N | Process Engineer |
| `renesaselectronics` | 2026-09-14 | Manufacturing | N | Process Engineer |
| `renesaselectronics` | 2026-09-14 | Manufacturing | N | Senior NPI Engineer |
| `renesaselectronics` | 2026-09-14 | Manufacturing | ? | Sr Product Testing Engineer |
| `renesaselectronics` | 2026-09-14 | Manufacturing | ? | Staff Product Testing Engineer |
| `renesaselectronics` | 2026-09-15 | Manufacturing | T | Senior/Staff/Sr Staff Engineer, Product Definer (IC Architect) |
| `renesaselectronics` | 2026-09-15 | Manufacturing | N | Test Process Engineer |
| `renesaselectronics` | 2026-09-15 | Manufacturing | N | Test Process Engineer |
| `renesaselectronics` | 2026-09-16 | Manufacturing | N | Impedance Sensor Application Engineer (f/m/d) |
| `renesaselectronics` | 2026-09-16 | Manufacturing | ? | Sr Staff Hardware Validation Engineer |
| `renesaselectronics` | 2026-09-22 | Manufacturing | N | NPI Test Process Engineer |
| `renesaselectronics` | 2026-09-22 | Manufacturing | N | Staff Test Process Engineer |
| `renesaselectronics` | 2026-09-23 | Manufacturing | N | Senior Staff Business Development Engineer - Motor Drive Solutions (f/m/d) |
| `renesaselectronics` | 2026-09-24 | Manufacturing | ? | Principal Engineer, Analog / Mixed-Signal IC Design |
| `renesaselectronics` | 2026-09-24 | Manufacturing | N | Sr Staff Product Engineer |
| `renesaselectronics` | 2026-09-03 | Sales | N | Senior Manager, Field Applications Engineering – Strategic Accounts (Power) |
| `seniorplc1` | 2026-02-04 | Manufacturing | N | Engineer - Engineering |
| `seniorplc1` | 2026-02-06 | Manufacturing | N | Assistant Manager - Engineering |
| `sikaag` | 2026-09-18 | Manufacturing | N | Technical Engineer |

</details>

## The served department

`department` is what the gate reads, and it is also what the served row shows (the Space's `/job` route and MCP `get_job`). It is a fact field, and a None overwrites the stored value. The first draft of this change set `department` to None whenever the function was not Information Technology. On the 244 walked Boards, 12,276 postings stay tech, and 6,041 of them state no department and show a non-IT function: `boschgroup` 1,018, `aecom2` 639, `renesaselectronics` 549, `jobsforhumanity` 230, `wabtec` 230, `egisgroup` 202. Each would have lost its department on its next scrape.

So `parse` also keeps every function's label as `Job.job_function`, and `doc_prep.stored_facts` shows it as the department when the posting states none. Replaying `parse` then `stored_facts` over all 66,209 walked postings gives the same served department as version 6 on every one. It also gives the same tech verdict as the IT-only rule on every one: 12,276 kept either way, 0 differences.

## Option D: the role veto reads plurals

`_NON_TECH_ROLE` ended in `)\b`, so rule 4 promoted "Welders" and "Security Officers" while refusing the singulars. With `)s?\b` it refuses 329 served rows on 22 Boards, every one in rule 4 (`tech-department` → `no-tech-signal`). Nothing enters. 311 are security officers and guards, 273 of them on `phenom:careers.sunstatessecurity.com`. Every title was read; the 18 that are not security staff:

| Board | department | title |
|---|---|---|
| `elidelprestige.zohorecruit.com` | Technology | Content Creators |
| `peoplique.zohorecruit.com` | Technology | Trainers/Instructors for IBM Maximo Application Suite Training |
| `peoplique.zohorecruit.com` | Technology | Trainers/Instructors for IBM Maximo Application Suite Training |
| `smartrecruiters:jobsforhumanity` | Engineering | Electricians |
| `smartrecruiters:jobsforhumanity` | Engineering | Facilities Electricians |
| `smartrecruiters:jobsforhumanity` | Engineering | Field Electricians |
| `abergeldiecomplex` | Infrastructure | HR Truck Drivers |
| `smartrecruiters:Infasta` | Information Technology | Content Creators Internship |
| `clera` | Engineering | Team Lead Account Executives |
| `biztekpeople.zohorecruit.com` | Technology | Customer Services Representatives - Utilities |
| `luxerone.zohorecruit.com` | Technology | Accountants Receivable Supervisor |
| `careers.sunstatessecurity.com` | Security Operations | Flex Security Drivers - $16.48/hr |
| `medpace` | Data Management | Clinical Data Review Associate (Mumbai) - Entry (Nurses wanted) |
| `brightermondaygen-kazi.zohorecruit.com` | Security/Law Enforcement | Bus Drivers |
| `synergy365.zohorecruit.com` | Security and Surveillance | Mobile Patrol Drivers (Full- time/ Part- time) |
| `careers.l3harris.com` | Information Technology | Associate, IT Customer Services |
| `ricoh.zohorecruit.com` | Technology | Customer Services Intern |
| `mydreamconnect.org.ng` | Software Development; Tech Bootcamp; Tech Training | 📢 Call for Volunteer Instructors – MyDreamConnect TECH Bootcamp (Cohort 3)! |

Arguable: "Associate, IT Customer Services" (L3Harris), the two IBM Maximo trainer rows and the bootcamp instructor. Each now gets the verdict its singular already got. `technician` is deliberately not a veto word, in either number.

Pluralising `_NON_SOFTWARE` the same way was measured and not done: 134 served rows would flip, mostly "Mechanics" and "Civils" titles but with software or systems work among them ("Flight Mechanics and Simulation Engineer", a KLA "Sr. Systems Design Engineer (Optics, Opto-Mechanics, Motion Control Systems)").

## Detail attempts

SmartRecruiters' pre-detail gate asks `filter_tech`'s question with the same department, so it moves with the fallback. The latest successful run before this change (36482634879) attempted 62,708 SmartRecruiters details. On the walked Boards the gate passes about 4,900 fewer postings, so a run whose slice holds all of them attempts about 57,800 (−8%). That figure is projected from the walk, not measured on a run. Read the join log's `smartrecruiters detail loss events … attempted` line after this ships; #570 closes once it has been read.
