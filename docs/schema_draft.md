# Draft schema and vocabulary

Exported 2026-10-27 from the planning doc's "Draft schema and vocabulary" tab and updated for vocabulary **v0.3** (2026-11-08, issue #14). The machine-readable version is `vocab/draft_vocabulary.yaml`; keep the two in sync. From v0.3, every closed-list term has a stable code (e.g. `MET-case-study`), and tags store the code (decision 0026). The term lists below show labels only; codes, definitions and `includes` are in the YAML.

The schema has 18 dimensions (17 in v0.2, plus additional sub-areas in v0.3, decision 0027). Five are taken directly from the 2025–2028 strategy (impact area, sub-area, objective, pathway to impact and research mode), so CDE's own categories become the backbone of the gap analysis. The rest cover the methods, places, systems and actors the strategy mentions without listing them. It's the starting point for curation: the terms are tested on the 200–300 record sample, and every change is logged. The schema table shows where each set of terms comes from; anything not taken from a CDE source is a proposal.

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

Topics CDE works on that no strategy sub-area covers (decision 0027). Each term has an `impact_area`: one of JE, SL, SG or TE when it belongs to that area but none of its sub-areas covers it; `cross_cutting` when it is relevant to several areas; `outside` when it is in none. Some terms carry `related` links to a sub-area or another term (e.g. Biodiversity conservation → SL-b); a related link is a pointer, not a merge. The v0.3 list has 77 terms, more than the 25–35 the decision aims for; precision in #16 decides whether it is narrowed.

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

Research mode uses the strategy's four modes. In v0.4 their labels and definitions were sharpened after the pilot review, with the codes unchanged (decision 0029), and the tagger may leave research mode empty for work that fits none of them. Methods started as a seed list of 34 terms in eight groups, drawn mostly from the methods the strategy names. Curation of the open-extraction candidates (v0.3) gives 60 terms in the same eight groups.

| Code | Research mode (strategy, section 1.3) | Definition |
| --- | --- | --- |
| M1 | Transformative research (co-creating change) | Researchers take part in the processes they study and co-create solutions with societal actors in specific contexts |
| M2 | Research on transformations (analytical) | Analyses the conditions, drivers and dynamics of societal change; theory development. Not for studies that do not address societal change |
| M3 | Transformative education (changing teaching) | Develops or reshapes curricula, courses and education systems to build competencies for transformation |
| M4 | Research on sustainability education | Studies how sustainability competencies can be fostered and assessed |

| Dimension | Group | Terms |
| --- | --- | --- |
| Method | Biophysical | Biodiversity field survey; Ecosystem service assessment and valuation; Field survey; Field visits and site inspections; Soil and water assessment; Soil spectroscopy (6) |
| Method | Geospatial and land | Database development; GIS and spatial analysis; Land degradation and resource assessment; Land-use and land-cover change mapping; Landscape visualization and appraisal; Remote sensing; Spatially explicit assessment; WOCAT methodology (8) |
| Method | Institutional and political analysis | Power-sensitive institutional analysis; Social network analysis; Stakeholder and actor analysis; Value chain analysis (4) |
| Method | Participatory and transdisciplinary | Academic and professional events; Collaborative learning; Deliberative formats; Knowledge co-production; Living labs and real-world labs; Multi-stakeholder processes; Participatory action research; Participatory mapping and appraisal; Participatory methods; Participatory workshops; Scenario development and analysis; Transdisciplinary research (12) |
| Method | Qualitative social science | Case study; Comparative case study; Discourse and narrative analysis; Document and policy analysis; Ethnography and participant observation; Expert interviews; Historical analysis; Interviews and focus groups; Legal analysis; Mixed-methods research; Qualitative content analysis (11) |
| Method | Quantitative social science | Econometrics; Expert surveys and assessments; Household and other surveys; Pilots and field experiments; Poverty and well-being measurement; Statistical analysis (6) |
| Method | Synthesis and theory | Conceptual and theoretical work; Conceptual modelling; Literature and systematic review; Policy brief and recommendation; Scientific assessment contributions (IPCC, IPBES, GEO); Scoping review; Synthesis workshops (7) |
| Method | Systems, assessment and evaluation | Hydrological modelling; Impact and sustainability assessment; Modelling and simulation; Monitoring, evaluation and learning; Systems analysis; Telecoupling analysis (6) |
| Data type | Documents, texts and media | Academic literature; Conference documents; Documents and policies; International legal instruments; Legal frameworks; Media articles; Policy documents; Project documentation; Project reports; Secondary qualitative; Visual data; Workshop documentation (12) |
| Data type | Environmental measurements | Climate and meteorological data; Climate scenarios; Hydrological data; Precipitation data; Sediment data; Soil data; Soil samples (7) |
| Data type | Geospatial and remote sensing | Aerial and drone imagery; Elevation data; Forest change data; Land cover and use data; Primary geospatial; Satellite imagery; Secondary geospatial; Soil maps; Species distribution data; Vegetation data (10) |
| Data type | No empirical data | None (conceptual) (1) |
| Data type | Primary social data | Case studies; Experiences; Expert knowledge; Interview data; Observation and field visits; Primary qualitative; Primary quantitative; Survey data (8) |
| Data type | Statistics and databases | Agricultural statistics; Curated databases; Demographic statistics; Land deal records; Secondary statistics; Socioeconomic data (6) |

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

Setting stays a dimension of its own (v0.3: 27 terms in five groups). Ten candidates that named places or scales (Africa, Switzerland, Global, …) were rejected, because country, region and scale already cover them. Mountains keep their own term because of CDE's long mountain focus through *Mountain Research and Development* and the Mountain Research Initiative.

| Dimension | Group | Terms |
| --- | --- | --- |
| Setting | Agricultural and rural landscapes | Agricultural landscapes; Heterogeneous landscapes; Rainfed agricultural areas; Rural areas; Rural lowlands; Smallholder farms (6) |
| Setting | Biomes and ecosystems | Amazon biome; Coastal and marine; Drylands and rangelands; Forest landscapes; Grasslands and steppes; Humid tropics and forest frontiers; Lowlands; Rivers and wetlands (8) |
| Setting | Mountains and highlands | Alpine and glacial landscapes; Andean highlands; High-altitude drylands; Highlands; Mountains; Steep and hilly terrain (6) |
| Setting | Protected and governed areas | Indigenous territories; Protected and conserved areas; Regional nature parks; World heritage sites (4) |
| Setting | Urban settings and institutions | Higher education institutions; Peri-urban; Urban (3) |
| Scale | — | Community or village; Cross-scale (incl. telecoupling); Global; Household or individual; Landscape or watershed; National; Subnational; Transboundary or regional (8) |

## Systems, actors and justice lenses

These three dimensions make gaps specific. Combined with methods, they answer questions such as "which actor groups do we study but rarely engage as co-producers?"

| Dimension | Group | Terms |
| --- | --- | --- |
| System | Economy and finance | Energy; Extractives and mining; Finance and investment; Hydropower; Infrastructure; Tourism; Trade (7) |
| System | Food and commodities | Agri-food value chains; Agricultural and commodity trade; Agricultural systems; Agriculture and food sector; Commodity value chains; Food systems; Food systems and agriculture; Irrigation systems; Large-scale land investments; Livestock systems; Mountain agriculture; Public catering; Rubber commodity system; Shifting cultivation systems; Smallholder farming systems; Urban food systems (16) |
| System | Governance and institutions | Biodiversity governance; Commons; Environmental governance; Governance systems; Land tenure and access; Natural resource management; Protected area governance; Spatial planning systems; Sustainable development frameworks; Water resource management (10) |
| System | Knowledge and education | Academic publishing; Agricultural knowledge systems; Higher education; Knowledge systems; Mountain research networks; Research networks and partnerships; Science systems and funding; Science-policy interfaces; Transdisciplinary research (9) |
| System | Land and ecosystems | Cropland; Ecosystem services; Ecosystems; Forests; Freshwater and waterbodies; Glaciers and cryosphere; Grasslands; Grazing land and rangelands; Hydrological systems; Land use and management systems; Landscape systems; Mountain ecosystems; Protected areas; Social-ecological systems; Soil systems; Telecoupled systems; Wetlands (17) |
| System | Settlements and society | Labour markets; Livelihoods and labour; Local communities; Regional development systems; Rural development; Social security and care; Urban areas and housing (7) |
| Actors | Civil society and science | Conservation NGOs; Conservation professionals; Development practitioners; Educators and lecturers; Legal professionals and scholars; Mountain Research Initiative; NGOs and civil society; Research and education partners; Research funders; Research institutions; Research networks; Researchers and universities; Soil and water conservation specialists; Sustainability practitioners and initiatives; Swiss National Science Foundation; WOCAT (16) |
| Actors | Justice groups | Displaced persons; Human rights and environmental defenders; Marginalized and vulnerable groups; Poor populations; Women; Youth and students (6) |
| Actors | Land users and communities | Agricultural practitioners and extension agents; Commoners; Farmer and producer organizations; Forest and land managers; Grassroots and urban-commons initiatives; Indigenous peoples and local communities; Local communities; Local institutions and organizations; Local populations; Pastoralists; Producers and producer organizations; Smallholders and land users; Villagers and village communities; Water users and managers (14) |
| Actors | Private sector | Agribusinesses; Companies and investors; Financial sector; Private sector; Social enterprises and cooperatives; Standard-setters; Swiss companies (7) |
| Actors | Public and households | Consumers; General public; Households (3) |
| Actors | State and international | Decision-makers; Development agencies; Donors and funders; European institutions and policymakers; Extension services; International donors and aid agencies; International organisations and UN conventions; International trade organisations; Municipal and local governments; National and local government; National environmental agencies; Nature park management; Policy makers; Regional and state authorities; Swiss government and development agencies (15) |
| Justice lens | — | Decolonial; Distributive; Gender; None explicit; Power; Procedural; Recognition (7) |

Each actor tag also records a **role**: *studied*, *engaged as co-producer* or *audience*. This is how the catalogue shows how transdisciplinary the work really is.

## Design decisions to record

Each item becomes a file in `docs/decisions/` once decided.

- [x] **Older work.** Should all publications be tagged with the 2025–2028 categories, or should earlier strategy categories be mapped across too? Decided in [0016](decisions/0016-older-work-current-categories.md): the current categories for all years, with the BORIS unit kept as recorded. This affects nearly the whole corpus: 268 publications date from 2025 or later, and 536 predate 2010.
- [x] **Partner regions.** Are there four or five, and which countries belong to each? Decided in [0017](decisions/0017-partner-regions.md): four regions plus a partner-country flag.
- [ ] **Objectives and pathways.** Can these be tagged reliably from publication text, or are they better recorded at project level from BORIS project records?
- [x] **Method granularity.** Settled in curation (v0.3, decision 0026): 60 terms in eight groups; groups have codes (`MET-G-…`), so gap counts can roll up to groups. System gained a sixth group (governance and institutions) and actors a sixth (public and households). Data type got six groups and setting five in v0.3; scale and justice lens are short enough to stay flat. Every other dimension has groups or a fixed parent (sub-area and additional sub-area → impact area; country → region).
- [ ] **SDGs.** Cap at three per record, or tag all that apply?
- [x] **Mountains and setting.** Settled in curation (v0.3): setting stays a dimension of its own, with 27 terms in five groups, including *Mountains and highlands*.
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
