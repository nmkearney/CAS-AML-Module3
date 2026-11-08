# Draft schema and vocabulary

Exported 2026-10-27 from the planning doc's "Draft schema and vocabulary" tab. The machine-readable version is `vocab/draft_vocabulary.yaml`; keep the two in sync.

This draft proposes 17 dimensions. Five are taken directly from the 2025–2028 strategy (impact area, sub-area, objective, pathway to impact and research mode), so CDE's own categories become the backbone of the gap analysis. The rest cover the methods, places, systems and actors the strategy mentions without listing them. It's the starting point for curation: the terms are tested on the 200–300 record sample, and every change is logged. The schema table shows where each set of terms comes from; anything not taken from a CDE source is a proposal.

## Schema

All dimensions except output type allow several values per record. Free-text keywords are also kept for search, but they aren't used for gap counts.

| # | Dimension | What it records | Source of terms | How it's filled |
| --- | --- | --- | --- | --- |
| 1 | Impact area | Which of CDE's four impact areas the work contributes to | Strategy, Table 1; BORIS unit | LLM, closed list; checked against BORIS unit where one exists (only ~600 of 2,507 publications) |
| 2 | Sub-area | Which of the 16 impact sub-areas | Strategy, Table 1 | LLM, closed list |
| 3 | Objective | Which of the five key objectives it serves | Strategy, section 1.4 | LLM, closed list |
| 4 | Pathway to impact | How the work aims to create change | Strategy, section 2.2 | LLM, closed list; tagged only when explicit |
| 5 | Research mode | Transformative or transformation research or education | Strategy, section 1.3 | LLM, closed list |
| 6 | Method | Research methods used | Proposed list below, refined on the sample | LLM open extraction, then curated |
| 7 | Data type | Kind of data the work rests on | Proposed list | LLM, closed list |
| 8 | Country | Countries studied | ISO 3166-1 alpha-3 | LLM, matched against a gazetteer |
| 9 | Region | World region and CDE partner region | UN M49; CDE partner regions | Derived from country by code |
| 10 | Setting | Biophysical or settlement setting (mountains, drylands, urban…) | Proposed list | LLM, closed list |
| 11 | Scale | Level of analysis | Proposed list | LLM, closed list |
| 12 | System | Land, resource, sector or institutional system studied | Strategy terms plus proposals | LLM, closed list |
| 13 | Actors | Actor groups, and whether they were studied or engaged as co-producers | Strategy terms plus proposals | LLM, closed list |
| 14 | Justice lens | Which justice, equity or power dimensions are addressed | Strategy, sections 1.4, 3.1 and 3.3 | LLM, closed list |
| 15 | SDG | Up to three Sustainable Development Goals | UN, 17 goals | LLM, closed list |
| 16 | Output type | Publication type and main audience | BORIS type; proposed audiences | Metadata; audience by LLM |
| 17 | Transformative aspiration | The stance the work takes towards existing systems: collaborate, challenge or disrupt | Westerink et al. 2026; see decision 0005 | LLM, closed list |

Dimensions 1–5 use the current strategy's categories for all years. Older work is tagged against categories that didn't exist when it was written, and the BORIS unit is kept as recorded; see decision 0016.

## Impact areas and sub-areas

The strategy defines four impact areas, each with four sub-areas, and BORIS records the same four units. The scope notes below summarise the strategy text and double as tagging definitions. Two names differ between the sources: BORIS calls one unit "Sustainable Governance" and another "Just Economies & Human Well Being". The tagger should treat these as the same as the strategy's "Sustainability governance" and "Just economies and human well-being".

| Code | Sub-area | Scope note for tagging |
| --- | --- | --- |
| **JE** | **Just economies and human well-being** | |
| JE-a | Human well-being on a healthy planet | Quality of life versus planetary boundaries; social–ecological interactions in mountains, nature parks and protected areas |
| JE-b | Just societies on global and local levels | Justice and inequality in transitions; distribution of scarce resources; concepts of well-being and prosperity |
| JE-c | Deliberation of future economies | Solidarity-based, growth-independent and regenerative economies; work, care and social security; visioning, scenarios and deliberative formats |
| JE-d | Pathways towards just and regenerative production and consumption systems | Real-world labs, incubators, start-ups, grassroots and urban-commons initiatives; niche innovations and their scaling |
| **SL** | **Sustainable land systems** | |
| SL-a | Sustainable land use | Cropland, grazing land, forests, wetlands and waterbodies; sustainable land management practices; WOCAT; land degradation neutrality |
| SL-b | Biodiversity and ecosystem services | Synergies and trade-offs, ecosystem service values and value pluralism; agroecology; land-use policy |
| SL-c | Multifunctional landscapes | Integrated landscape planning across sectors; participatory visioning; blended, private-sector and impact finance |
| SL-d | Catalysts of land system change | Tenure security, gender, power asymmetries, telecoupling; change agents and social learning |
| **SG** | **Sustainability governance** | |
| SG-a | Public governance of sustainable economy, including trade and finance | Trade rules and model rules for agricultural trade; international and illicit financial flows; due diligence regulation |
| SG-b | Sustainability governance in the private sector | Certification, corporate sustainability programmes, social enterprises, cooperatives, direct trade; greenwashing; pricing and procurement |
| SG-c | Transparency and accountability mechanisms | Land Matrix and large-scale land investments; multi-stakeholder platforms (cocoa, coffee, seafood); monitoring, evaluation and learning |
| SG-d | Community-based and collaborative governance | Indigenous and local knowledge; self-organised governance of fisheries, agroecology, housing and green spaces; mega-infrastructure |
| **TE** | **Transformative education and science** | |
| TE-a | Transformative learning and teaching for change | Curricula, competencies and change agency; bachelor's, master's, PhD and CAS programmes; decolonisation and inclusive education |
| TE-b | Spaces for science–society collaboration | Living labs, forums, knowledge and stakeholder platforms; methods for joint learning; student-led innovation |
| TE-c | Higher education for sustainable development | Whole-institution approach at UniBe; sustainability-oriented universities; summer schools; training of trainers |
| TE-d | Science–policy engagement for sustainability transformations | Policy forums, scientific assessments, research funding schemes; networks such as GLP, WOCAT, MRI and Earth System Governance |

## Objectives and pathways to impact

The objectives cut across all four impact areas. Combined with the sub-areas, they show, for example, how much land-systems work serves just and resource-light economies.

| Code | Objective (strategy, section 1.4) |
| --- | --- |
| O1 | Ecosystem regeneration for sustainable livelihoods |
| O2 | Sustainable food systems and land investments |
| O3 | Just and resource-light economies |
| O4 | Institutions for sustainability |
| O5 | Competencies for sustainability transformations |

Pathways describe how a piece of work aims to create change. Publications often don't state this, so the tagger records a pathway only when the text makes it explicit, and leaves it empty otherwise.

| Code | Pathway (strategy, section 2.2) | Tag when the work… |
| --- | --- | --- |
| P1 | Knowledge-informed decision-making | produces evidence meant to inform policy or practice decisions, including syntheses and assessments |
| P2 | Transformative literacy | builds people's capacity to understand and take part in transformation: teaching, training, public communication |
| P3 | Institutional work | shapes strategies, rules or norms, or convenes multi-stakeholder or science–policy processes |
| P4 | Social and technological innovations | pilots, scales or analyses new practices, tools or technologies |
| P5 | Partnerships in regions | is built on, or explicitly strengthens, long-term regional partnerships |

## Research mode and methods

Research mode uses the strategy's four definitions. Methods are a proposed seed list of 34 terms in eight groups, drawn mostly from the methods the strategy names. The open extraction on the sample will show which terms are missing and which are never used.

| Code | Research mode (strategy, section 1.3) | Definition |
| --- | --- | --- |
| M1 | Transformative research | Researchers take part in the processes they study and co-create solutions in specific contexts |
| M2 | Transformation research | Analyses the conditions, characteristics and drivers of societal and environmental change; contributes to theory |
| M3 | Transformative education | Reshapes education systems and curricula to build competencies for transformation |
| M4 | Transformation education | Investigates how sustainability competencies can be fostered and assessed |

| Method group | Terms |
| --- | --- |
| Participatory and transdisciplinary | Knowledge co-production; participatory action research; living labs and real-world labs; multi-stakeholder processes; futures methods (visioning, scenarios, futures workshops); deliberative formats |
| Qualitative social science | Interviews and focus groups; ethnography and participant observation; case study; document and policy analysis; discourse and narrative analysis; legal analysis |
| Quantitative social science | Household and other surveys; econometrics; impact evaluation; poverty and well-being measurement; pilots and field experiments |
| Institutional and political analysis | Power-sensitive institutional analysis; stakeholder and actor analysis; value chain analysis |
| Geospatial and land | Remote sensing; GIS and spatial analysis; land-use and land-cover change mapping; database development (e.g. Land Matrix, WOCAT) |
| Biophysical | Soil and water assessment; biodiversity field survey; ecosystem service assessment and valuation |
| Systems, assessment and evaluation | Systems analysis; modelling and simulation; impact and sustainability assessment; monitoring, evaluation and learning |
| Synthesis and theory | Literature and systematic review; scientific assessment contributions (IPCC, IPBES, GEO); conceptual and theoretical work |

**Data type:** primary qualitative · primary quantitative · primary geospatial · secondary statistics · secondary geospatial (e.g. satellite products) · documents and policies · curated databases · none (conceptual work).

## Transformation aspirations

## Regions, setting and scale

Country is the base unit. Both region layers are derived from it in code, so the LLM never assigns a region directly.

- **Country:** ISO 3166-1 alpha-3 codes, plus a "global or multi-country" flag for work with no single study country.
- **World region:** UN M49 sub-regions (Eastern Africa, South-eastern Asia, South America, Western Europe…).
- **CDE partner region:** four regions mapped from country (decision 0017). CDE's sources don't fully agree: the strategy web page says "four world regions" without naming them; the regions page names Eastern Africa, mainland Southeast Asia and South America, plus Europe and Switzerland; the 2020 annual report names East Africa, the Horn of Africa, mainland Southeast Asia and the central Andes.
- **CDE partner country:** a flag for the seven countries with a main partner institution: Kenya (CETRAD, University of Nairobi), Ethiopia (WLRC), Laos (National University of Laos), Myanmar (Land Core Group), Nepal (ICIMOD, Nepal Centre for Contemporary Research), Bolivia (CIDES-UMSA) and Madagascar (ESSA-Forêts, Université d'Antananarivo).

| Code | CDE partner region | Countries |
| --- | --- | --- |
| CDE-EAF | Eastern Africa | UN M49 Eastern Africa (014), including the Horn of Africa and Madagascar |
| CDE-MSEA | Mainland Southeast Asia | Cambodia, Laos, Myanmar, Thailand, Viet Nam |
| CDE-SAM | South America | UN M49 South America (005), including the central Andes |
| CDE-EUR | Europe and Switzerland | UN M49 Europe (150) |

Countries outside these regions, such as Nepal, keep their country and M49 tags but get no partner region.

**Setting** (proposed): mountains · drylands and rangelands · humid tropics and forest frontiers · coastal and marine · rural lowlands · peri-urban · urban. Mountains get their own term because of CDE's long mountain focus through *Mountain Research and Development* and the Mountain Research Initiative.

**Scale** (proposed): household or individual · community or village · landscape or watershed · subnational · national · transboundary or regional · global · cross-scale, for work that explicitly links levels, including telecoupling.

## Systems, actors and justice lenses

These three dimensions make gaps specific. Combined with methods, they answer questions such as "which actor groups do we study but rarely engage as co-producers?"

| Dimension | Group | Terms |
| --- | --- | --- |
| System | Land and ecosystems | Cropland; grazing land and rangelands; forests; wetlands; freshwater and waterbodies; protected areas |
| System | Food and commodities | Food systems and agriculture; commodity value chains (cocoa, coffee, seafood, others); large-scale land investments |
| System | Economy and finance | Trade; finance and investment; extractives and mining; energy; infrastructure |
| System | Settlements and society | Urban areas and housing; livelihoods and labour; social security and care |
| System | Knowledge and education | Higher education; science systems and funding; science–policy interfaces |
| Actors | Land users and communities | Smallholders and land users; pastoralists; Indigenous peoples and local communities; grassroots and urban-commons initiatives |
| Actors | Groups the strategy names for justice | Women; displaced persons; human rights and environmental defenders; youth and students |
| Actors | State and international | National and local government; extension services; international organisations and UN conventions |
| Actors | Private sector | Companies and investors; social enterprises and cooperatives; standard-setters; financial sector |
| Actors | Civil society and science | NGOs and civil society; researchers and universities |
| Justice lens | — | Distributive (inequality, winners and losers); procedural (participation, voice); recognition (Indigenous and local knowledge); gender; power (power over or power with); decolonial; none explicit |

Each actor tag also records a **role**: *studied*, *engaged as co-producer* or *audience*. This is how the catalogue shows how transdisciplinary the work really is.

## Design decisions to record

Each item becomes a file in `docs/decisions/` once decided.

- [x] **Older work.** Should all publications be tagged with the 2025–2028 categories, or should earlier strategy categories be mapped across too? Decided in [0016](decisions/0016-older-work-current-categories.md): the current categories for all years, with the BORIS unit kept as recorded. This affects nearly the whole corpus: 268 publications date from 2025 or later, and 536 predate 2010.
- [x] **Partner regions.** Are there four or five, and which countries belong to each? Decided in [0017](decisions/0017-partner-regions.md): four regions plus a partner-country flag.
- [ ] **Objectives and pathways.** Can these be tagged reliably from publication text, or are they better recorded at project level from BORIS project records?
- [ ] **Method granularity.** Are 34 terms in eight groups the right level of detail, or should gap counts use the eight groups and keep terms for drill-down?
- [ ] **SDGs.** Cap at three per record, or tag all that apply?
- [ ] **Mountains and setting.** Is setting worth its own dimension, or is a mountain flag enough?
- [ ] **Actor roles.** Is studied, engaged or audience a distinction CDE wants to measure, given how sensitive a "transdisciplinarity gap" could be?
- [ ] **Output audience.** Add academic, policy, practitioner and public as audiences next to the BORIS publication type?

## Sources

- CDE (2025). *Strategy 2025–2028: Inspiring Sustainable Futures*: sections 1.3, 1.4, 2.2 and 3, Table 1.
- [CDE strategy web page](https://www.cde.unibe.ch/about_us/portrait/strategy/index_eng.html)
- [CDE's worldwide activities](https://cde.unibe.ch/research/regions/index_eng.html)
- [CDE partners and networks](https://www.cde.unibe.ch/about_us/organization/partners__networks/index_eng.html)
- [CDE Annual Report 2020: key partner regions](https://annualreport2020.cde.unibe.ch/pages/key-partner-regions/detailstory.html)
- [Centre for Development and Environment (Wikipedia)](<https://en.wikipedia.org/wiki/Centre_for_Development_and_Environment_(Switzerland)>), for earlier research areas
- BORIS Portal unit pages, e.g. [CDE – Sustainable Land Systems](https://boris-portal.unibe.ch/handle/20.500.12422/30981) and [CDE – Sustainable Governance](https://boris-portal.unibe.ch/handle/20.500.12422/30635)
- [Transformative pathways for social networks to navigate towards a nature-positive society: collaborate, challenge, and disrupt](https://www.sciencedirect.com/science/article/pii/S1877343526000138)
