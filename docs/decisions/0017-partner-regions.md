# 0017: Four CDE partner regions, derived from country, plus a partner-country flag

- Status: accepted
- Date: 2026-11-07

## Context
The schema's region dimension has two layers, both derived from country in code: UN
M49 sub-regions and CDE's own partner regions. CDE's sources (read 2026-11-07) do not
agree on the partner regions:

- [Strategy web page](https://www.cde.unibe.ch/about_us/portrait/strategy/index_eng.html):
  "four world regions" under the pathway *Partnerships in regions*, not named.
- [Regions page](https://cde.unibe.ch/research/regions/index_eng.html): key regions
  Eastern Africa, Southeast Asia ("mainland Southeast Asia, especially Lao PDR and
  Myanmar") and South America (Peru), plus Europe and Switzerland. Its menu also has a
  "Mountains" tab.
- [Annual Report 2020](https://annualreport2020.cde.unibe.ch/pages/key-partner-regions/detailstory.html):
  East Africa, the Horn of Africa, mainland Southeast Asia and the central Andes.
- [Partners page](https://www.cde.unibe.ch/about_us/organization/partners__networks/index_eng.html):
  main partner institutions in Kenya, Ethiopia, Laos, Myanmar, Nepal, Bolivia and
  Madagascar. It does not group them by region.

The strategy draft also mentions "five regions". The earlier draft in the vocabulary
had six regions, including Madagascar and South Asia as their own regions.

## Decision
- **Four partner regions**, matching the strategy's "four world regions" and the
  current regions page:

  | Code | Label | Countries |
  |---|---|---|
  | `CDE-EAF` | Eastern Africa | UN M49 Eastern Africa (014), which includes the Horn of Africa and Madagascar |
  | `CDE-MSEA` | Mainland Southeast Asia | KHM, LAO, MMR, THA, VNM |
  | `CDE-SAM` | South America | UN M49 South America (005), which includes the central Andes |
  | `CDE-EUR` | Europe and Switzerland | UN M49 Europe (150) |

- **Derived in code from country tags.** The LLM tags countries only; it never assigns
  a region.
- **Other countries get no partner region.** They keep their country and UN M49
  sub-region tags. Nepal, for example, is in Southern Asia but in no partner region.
- **A separate partner-country flag** (`cde_partner_country`) marks the seven
  countries with a main partner institution: KEN, ETH, LAO, MMR, NPL, BOL, MDG.
- **Codes carry a `CDE-` prefix** so they never clash with M49 names ("Eastern Africa"
  is both).
- The mapping lives in `vocab/draft_vocabulary.yaml` (`region.cde_partner_region`,
  `region.cde_partner_countries`).

## Consequences
- Regional gap questions ("where have we worked on water governance?") can be asked at
  three levels: country, M49 sub-region, partner region. The partner-country flag
  answers "in our partner countries" without stretching a region to fit Nepal.
- Using whole M49 regions makes partner regions broad: Eastern Africa includes, for
  example, Mozambique and Zimbabwe. Answers should say that the region is defined by
  M49.
- The derivation code and an M49 table are part of tagging (#15).
- The test fixtures' region tags were renamed to the new codes (`tests/fixtures/tags.jsonl`
  and `tag_review.csv`, status `changed`). Nepal's old "South Asia and Himalaya" tag
  became the M49 sub-region "Southern Asia".
- Revisit:
  - confirm the regions and countries with CDE management;
  - whether "Mountains" should be a region or stay a `setting` term;
  - narrower regions (Horn of Africa, central Andes) if answers are too coarse.
