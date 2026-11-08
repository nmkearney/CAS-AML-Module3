# Draft schema and vocabulary

Exported 2026-10-27 from the planning doc's "Draft schema and vocabulary" tab and updated for vocabulary **v0.3** (2026-11-08, issue #14). The machine-readable version is `vocab/draft_vocabulary.yaml`; keep the two in sync. From v0.3, every closed-list term has a stable code (e.g. `MET-case-study`), and tags store the code (decision 0025). The term lists below show labels only; codes, definitions and `includes` are in the YAML.

The schema has 18 dimensions (17 in v0.2, plus additional sub-areas in v0.3, decision 0026). Five are taken directly from the 2025–2028 strategy (impact area, sub-area, objective, pathway to impact and research mode), so CDE's own categories become the backbone of the gap analysis. The rest cover the methods, places, systems and actors the strategy mentions without listing them. It's the starting point for curation: the terms are tested on the 200–300 record sample, and every change is logged. The schema table shows where each set of terms comes from; anything not taken from a CDE source is a proposal.

## Schema

All dimensions except output type allow several values per record. Free-text keywords are also kept for search, but they aren't used for gap counts.

| # | Dimension | What it records | Source of terms | How it's filled |
| --- | --- | --- | --- | --- |
| 1 | Impact area | Which of CDE's four impact areas the work contributes to | Strategy, Table 1; BORIS unit | Derived in code from the parents of the sub-area and additional sub-area tags (proposed in #49; implemented with tagging, #15); checked against BORIS unit where one exists |
| 2 | Sub-area | Which of the 16 impact sub-areas | Strategy, Table 1 | LLM, closed list |
| 3 | Additional sub-area | Topics CDE works on that no strategy sub-area covers, each with a parent impact area, `cross_cutting` or `outside` | Open extraction on the sample (theme candidates), curated | LLM, closed list; tagged independently of sub-area |
| 4 | Objective | Which of the five key objectives it serves | Strategy, section 1.4 | LLM, closed list |
| 5 | Pathway to impact | How the work aims to create change | Strategy, section 2.2 | LLM, closed list; tagged only when explicit |
| 6 | Research mode | Transformative or transformation research or education | Strategy, section 1.3 | LLM, closed list |
| 7 | Method | Research methods used | Proposed list below, refined on the sample | LLM open extraction, then curated |
| 8 | Data type | Kind of data the work rests on | Proposed list | LLM, closed list |
| 9 | Country | Countries studied | ISO 3166-1 alpha-3 | LLM, matched against a gazetteer |
| 10 | Region | World region and CDE partner region | UN M49; CDE partner regions | Derived from country by code |
| 11 | Setting | Biophysical or settlement setting (mountains, drylands, urban…) | Proposed list | LLM, closed list |
| 12 | Scale | Level of analysis | Proposed list | LLM, closed list |
| 13 | System | Land, resource, sector or institutional system studied | Strategy terms plus proposals | LLM, closed list |
| 14 | Actors | Actor groups, and whether they were studied or engaged as co-producers | Strategy terms plus proposals | LLM, closed list |
| 15 | Justice lens | Which justice, equity or power dimensions are addressed | Strategy, sections 1.4, 3.1 and 3.3 | LLM, closed list |
| 16 | SDG | Up to three Sustainable Development Goals | UN, 17 goals | LLM, closed list |
| 17 | Output type | Publication type and main audience | BORIS type; proposed audiences | Metadata; audience by LLM |
| 18 | Transformative aspiration | The stance the work takes towards existing systems: collaborate, challenge or disrupt | Westerink et al. 2026; see decision 0005 | LLM, closed list |

Dimensions 1, 2 and 4–6 use the current strategy's categories for all years. Older work is tagged against categories that didn't exist when it was written, and the BORIS unit is kept as recorded; see decision 0016.

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

## Additional sub-areas

Topics CDE works on that no strategy sub-area covers (decision 0026). Each term has an `impact_area`: one of JE, SL, SG or TE when it belongs to that area but none of its sub-areas covers it; `cross_cutting` when it is relevant to several areas; `outside` when it is in none. Some terms carry `related` links to a sub-area or another term (e.g. Biodiversity conservation → SL-b); a related link is a pointer, not a merge. The v0.3 list has 77 terms, more than the 25–35 the decision aims for; precision in #16 decides whether it is narrowed.

| impact_area | Additional sub-areas |
| --- | --- |
| JE | Climate and environmental justice; Food waste; Gender and labor; Indigenous rights and identity; Poverty reduction; Resource consumption; Socioeconomic drivers and marginality; Sustainable consumption and production; Urban food systems (9) |
| SL | Agricultural development and planning; Agricultural expansion; Agrobiodiversity and agroecology; Biodiversity conservation; Carbon sequestration; Cash crop dynamics; Conservation policy and interventions; Conservation-development trade-offs; Deforestation and forest degradation; Environmental degradation and impacts; Invasive species; Irrigation; Land and soil degradation; Land restoration; Land use and cover change; Land use competition; Land use intensification; Sediment dynamics; Shifting cultivation; Soil and water conservation; Soil erosion; Spatial and land use planning; Sustainable land management; Sustainable natural resource management; Telecoupling; Water resources management (26) |
| SG | Environmental policy design and implementation; Forest governance and policy; Governance structures and processes; Natural resource governance; Participatory governance; Policy coherence and synergies; Protected area governance; Public procurement; Sustainability standards and certification; Sustainable trade; Trade policy and agreements (11) |
| TE | Inter- and transdisciplinary collaboration; Knowledge co-production; Knowledge exchange and communication; Research funding policy; Sustainability competencies (5) |
| cross_cutting | Adaptive and transformative capacity; Climate change; Climate change adaptation; Climate change mitigation; Food security; Global change syndromes; Greenhouse gas emissions; Human-environment interactions; Land and natural resource governance; Land tenure and rights; Large-scale land acquisitions; Local and indigenous knowledge; Regional development; Smallholder agriculture; Social norms and institutions; Societal transformation; Stakeholder engagement; Sustainable development; Sustainable food systems; Sustainable mountain development; Trade-offs and co-benefits (21) |
| outside | Energy transition; Glaciers and snow cover; Hydrological processes; Population mobility; Sustainable mobility (5) |

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

Research mode uses the strategy's four definitions. Methods started as a seed list of 34 terms in eight groups, drawn mostly from the methods the strategy names. Curation of the open-extraction candidates (v0.3) gives 60 terms; the terms added in v0.3 are not yet grouped.

| Code | Research mode (strategy, section 1.3) | Definition |
| --- | --- | --- |
| M1 | Transformative research | Researchers take part in the processes they study and co-create solutions in specific contexts |
| M2 | Transformation research | Analyses the conditions, characteristics and drivers of societal and environmental change; contributes to theory |
| M3 | Transformative education | Reshapes education systems and curricula to build competencies for transformation |
| M4 | Transformation education | Investigates how sustainability competencies can be fostered and assessed |

| Dimension | Group | Terms |
| --- | --- | --- |
| Method | Biophysical | Biodiversity field survey; Ecosystem service assessment and valuation; Soil and water assessment (3) |
| Method | Geospatial land | Database development; GIS and spatial analysis; Land-use and land-cover change mapping; Remote sensing (4) |
| Method | Institutional political analysis | Power-sensitive institutional analysis; Stakeholder and actor analysis; Value chain analysis (3) |
| Method | Participatory transdisciplinary | Deliberative formats; Knowledge co-production; Living labs and real-world labs; Multi-stakeholder processes; Participatory action research (5) |
| Method | Qualitative social science | Case study; Discourse and narrative analysis; Document and policy analysis; Ethnography and participant observation; Interviews and focus groups; Legal analysis (6) |
| Method | Quantitative social science | Econometrics; Household and other surveys; Pilots and field experiments; Poverty and well-being measurement (4) |
| Method | Synthesis theory | Conceptual and theoretical work; Literature and systematic review; Scientific assessment contributions (IPCC, IPBES, GEO) (3) |
| Method | Systems assessment evaluation | Impact and sustainability assessment; Modelling and simulation; Monitoring, evaluation and learning; Systems analysis (4) |
| Method | — (not grouped) | Academic and professional events; Collaborative learning; Comparative case study; Conceptual modelling; Expert interviews; Expert surveys and assessments; Field survey; Field visits and site inspections; Historical analysis; Hydrological modelling; Land degradation and resource assessment; Landscape visualization and appraisal; Mixed-methods research; Participatory mapping and appraisal; Participatory methods; Participatory workshops; Policy brief and recommendation; Qualitative content analysis; Scenario development and analysis; Scoping review; Social network analysis; Soil spectroscopy; Spatially explicit assessment; Statistical analysis; Synthesis workshops; Telecoupling analysis; Transdisciplinary research; WOCAT methodology (28) |
| Data type | — | Academic literature; Aerial and drone imagery; Agricultural statistics; Case studies; Climate and meteorological data; Climate scenarios; Conference documents; Curated databases; Demographic statistics; Documents and policies; Elevation data; Experiences; Expert knowledge; Forest change data; Hydrological data; International legal instruments; Interview data; Land cover and use data; Land deal records; Legal frameworks; Media articles; None (conceptual); Observation and field visits; Policy documents; Precipitation data; Primary geospatial; Primary qualitative; Primary quantitative; Project documentation; Project reports; Satellite imagery; Secondary geospatial; Secondary qualitative; Secondary statistics; Sediment data; Socioeconomic data; Soil data; Soil maps; Soil samples; Species distribution data; Survey data; Vegetation data; Visual data; Workshop documentation (44) |

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

Setting stays a dimension of its own (v0.3: 35 terms); mountains keep their own term because of CDE's long mountain focus through *Mountain Research and Development* and the Mountain Research Initiative.

| Dimension | Group | Terms |
| --- | --- | --- |
| Setting | — | Africa; Agricultural landscapes; Alpine and glacial landscapes; Amazon biome; Andean highlands; Coastal and marine; Developing countries; Drylands and rangelands; Forest landscapes; Global; Global economic systems; Global North and South; Grasslands and steppes; Heterogeneous landscapes; High-altitude drylands; Higher education institutions; Highlands; Humid tropics and forest frontiers; Indigenous territories; Lowlands; Mainland Southeast Asia; Mountains; National and subnational scales; Peri-urban; Protected and conserved areas; Rainfed agricultural areas; Regional nature parks; Rivers and wetlands; Rural areas; Rural lowlands; Smallholder farms; Steep and hilly terrain; Switzerland; Urban; World heritage sites (35) |
| Scale | — | Community or village; Cross-scale (incl. telecoupling); Global; Household or individual; Landscape or watershed; National; Subnational; Transboundary or regional (8) |

## Systems, actors and justice lenses

These three dimensions make gaps specific. Combined with methods, they answer questions such as "which actor groups do we study but rarely engage as co-producers?"

| Dimension | Group | Terms |
| --- | --- | --- |
| System | Economy and finance | Energy; Extractives and mining; Finance and investment; Infrastructure; Trade (5) |
| System | Food and commodities | Commodity value chains; Food systems and agriculture; Large-scale land investments (3) |
| System | Knowledge and education | Higher education; Science systems and funding; Science-policy interfaces (3) |
| System | Land and ecosystems | Cropland; Forests; Freshwater and waterbodies; Grazing land and rangelands; Protected areas; Wetlands (6) |
| System | Settlements and society | Livelihoods and labour; Social security and care; Urban areas and housing (3) |
| System | — (not grouped) | Academic publishing; Agri-food value chains; Agricultural and commodity trade; Agricultural knowledge systems; Agricultural systems; Agriculture and food sector; Biodiversity governance; Commons; Ecosystem services; Ecosystems; Environmental governance; Food systems; Glaciers and cryosphere; Governance systems; Grasslands; Hydrological systems; Hydropower; Irrigation systems; Knowledge systems; Labour markets; Land tenure and access; Land use and management systems; Landscape systems; Livestock systems; Local communities; Mountain agriculture; Mountain ecosystems; Mountain research networks; Natural resource management; Protected area governance; Public catering; Regional development systems; Research networks and partnerships; Rubber commodity system; Rural development; Shifting cultivation systems; Smallholder farming systems; Social-ecological systems; Soil systems; Spatial planning systems; Sustainable development frameworks; Telecoupled systems; Tourism; Transdisciplinary research; Urban food systems; Water resource management (46) |
| Actors | Civil society and science | NGOs and civil society; Researchers and universities (2) |
| Actors | Justice groups | Displaced persons; Human rights and environmental defenders; Women; Youth and students (4) |
| Actors | Land users and communities | Grassroots and urban-commons initiatives; Indigenous peoples and local communities; Pastoralists; Smallholders and land users (4) |
| Actors | Private sector | Companies and investors; Financial sector; Social enterprises and cooperatives; Standard-setters (4) |
| Actors | State and international | Extension services; International organisations and UN conventions; National and local government (3) |
| Actors | — (not grouped) | Agribusinesses; Agricultural practitioners and extension agents; Commoners; Conservation NGOs; Conservation professionals; Consumers; Decision-makers; Development agencies; Development practitioners; Donors and funders; Educators and lecturers; European institutions and policymakers; Farmer and producer organizations; Forest and land managers; General public; Households; International donors and aid agencies; International trade organisations; Legal professionals and scholars; Local communities; Local institutions and organizations; Local populations; Marginalized and vulnerable groups; Mountain Research Initiative; Municipal and local governments; National environmental agencies; Nature park management; Policy makers; Poor populations; Private sector; Producers and producer organizations; Regional and state authorities; Research and education partners; Research funders; Research institutions; Research networks; Soil and water conservation specialists; Sustainability practitioners and initiatives; Swiss companies; Swiss government and development agencies; Swiss National Science Foundation; Villagers and village communities; Water users and managers; WOCAT (44) |
| Justice lens | — | Decolonial; Distributive; Gender; None explicit; Power; Procedural; Recognition (7) |

Each actor tag also records a **role**: *studied*, *engaged as co-producer* or *audience*. This is how the catalogue shows how transdisciplinary the work really is.

## Design decisions to record

Each item becomes a file in `docs/decisions/` once decided.

- [x] **Older work.** Should all publications be tagged with the 2025–2028 categories, or should earlier strategy categories be mapped across too? Decided in [0016](decisions/0016-older-work-current-categories.md): the current categories for all years, with the BORIS unit kept as recorded. This affects nearly the whole corpus: 268 publications date from 2025 or later, and 536 predate 2010.
- [x] **Partner regions.** Are there four or five, and which countries belong to each? Decided in [0017](decisions/0017-partner-regions.md): four regions plus a partner-country flag.
- [ ] **Objectives and pathways.** Can these be tagged reliably from publication text, or are they better recorded at project level from BORIS project records?
- [x] **Method granularity.** Settled in curation (v0.3, decision 0025): 60 terms, of which 32 sit in the eight groups; groups have codes (`MET-G-…`), so gap counts can roll up to groups. The new terms still need a group.
- [ ] **SDGs.** Cap at three per record, or tag all that apply?
- [x] **Mountains and setting.** Settled in curation (v0.3): setting stays a dimension of its own, with 35 terms including mountains.
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
