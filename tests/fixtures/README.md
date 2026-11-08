# Test fixtures

A small stand-in catalogue so Track B can build and test the store, tools and agent
before Track A's real pipeline exists (issue #3). It holds 30 real CDE publications
from BORIS and 4 synthetic records, in the formats of `src/chatcde/schemas.py`.
Design choices are recorded in `docs/decisions/0007-test-fixtures.md`.

## Contents

| File | Model / purpose | Rows |
|---|---|---|
| `records.jsonl` | `Record` | 34 |
| `authors.jsonl` | `Author` | 141 |
| `pages.jsonl` | `PageText`: pages 1–3 plus the first 3 body pages of each real PDF (PyMuPDF), 1 synthetic page | 127 |
| `downloads.jsonl` | `ManifestEntry` | 34 |
| `tags.jsonl` | `Tag`: hand-made tags on all 18 dimensions | 654 |
| `tag_review.csv` | One row per tag: hand-check status, reviewer, date, note | 654 |
| `body_pages.csv` | First body page per record (`auto` heuristic or `manual` override) | 30 |
| `synthetic.yaml` | Source of the 4 synthetic records | – |
| `selection.csv` | Output of the selection script: 30 picks + 90 reserves | – |
| `excluded.csv` | Records dropped in review, with reasons | – |
| `raw/<uuid>/` | BORIS item and bitstream JSON, page text, fetch summary | – |
| `raw/fetch_log.csv` | Kept/dropped decision for every candidate tried | – |
| `build_fixture_db.py` | Builds `fixture.duckdb` (gitignored) from the JSONL files | – |

In tests, use the `fixture_db` fixture from `tests/conftest.py`. It builds a fresh
DuckDB once per session and returns its path:

```python
def test_something(fixture_db):
    con = duckdb.connect(str(fixture_db), read_only=True)
```

Tables: `records`, `authors`, `tags`, `pages`, `downloads`. Lists are `VARCHAR[]`,
`provenance` is a `STRUCT`, timestamps are UTC `TIMESTAMP`.

## How the 30 were chosen

1. `scripts/select_fixture_records.py` (seed 3) took the 1,335 open-access publications
   in `data/cde_records.csv` (inventory of 2026-11-04). It covered every value on four
   axes (first CDE unit, decade, language, BORIS type), rarest first, then filled the
   rest weighted by √(pool share), so common values still dominate.
2. `scripts/fetch_fixture_records.py` (UniBe VPN, 2026-11-06) went through the picks
   and reserves in rank order and kept a record only if its main PDF carries an
   accepted licence (CC BY, BY-SA, BY-NC, BY-NC-SA, CC0; see decision 0007), has a text
   layer, and has at least 1,500 characters on its first 3 pages.
3. Review: four records whose first pages were covers or almost empty were excluded
   (`excluded.csv`) and replaced by the next reserves.
4. Body pages: for 18 records the first pages are mostly front matter (covers,
   imprints, contents, publisher notices). Pages 1–3 are kept, because the parse
   stage and Track B's chunking must cope with front matter, and the first 3 pages of
   the body are added, with their real page numbers (e.g. 1, 2, 3, 5, 6, 7). The body
   start is the first page with ≥ 1,500 characters and at most one front-matter
   marker; `body_pages.csv` records it, and `source=manual` rows override the
   heuristic (4 records: two BioOne cover sheets, a contributor list and a contents
   page that the heuristic took for body text).

Of 91 candidates tried, 30 were kept. Drops: 26 had only BORIS's "free to read"
statement, 14 no licence, 12 CC BY-NC-ND, 4 no PDF, 1 too little text, 4 excluded
in review. "Open access" in BORIS mostly means free to read, not openly licensed.

### Strata in the final 30, and where they are thin

| Axis | Values (count) |
|---|---|
| First CDE unit | none 13, Sustainable Governance 6, Just Economies & HWB 5, Transformative Education & Science 3, Services 2, **Sustainable Land Systems 1** |
| Decade | 2020s 16, 2010s 12, **2000s 2**, **before 2000: 0** |
| Language (BORIS) | en 19, de 6, `me` 5 (miscoded; all German), **fr 0, es 0** |
| Type | article 11, working_paper 6, report 5, magazine_article 3, conference_item 2, book 1, book_section 1, journal_series 1, **other 0** |
| Licence | CC BY 4.0 27, CC BY-NC 4.0 3 |

- Records before 2000 carry no open licence in BORIS, so the fixtures have none.
- `doi` is BORIS's own DOI (all 30 real records have one). 12 of the 30 also have a
  `publisher_doi`; the synthetic records have none.
- French appears only as a second language: `82d7bbd0` is coded `de; fr` and
  `d012a8d7` is coded `me`, and both PDFs are the French edition of a German report.
- Many records have one unit-less CDE author, so the unit axis is uneven; this mirrors
  BORIS, where most publications carry no unit.
- `9db04558` (2003) is old OCR text with broken spacing: useful for testing robustness.
- `dac3d725` and `7ab97dd8` are one-page conference abstracts.

## Synthetic records

Made-up titles, authors and text (`synthetic.yaml`), IDs `fixture-synthetic-NN`:

| ID | Edge case |
|---|---|
| `fixture-synthetic-01` | Restricted: file exists but download failed (`restricted`); BORIS abstract; DOI |
| `fixture-synthetic-02` | Metadata only: no file, no abstract; tags from title/keywords only, one at confidence 0.3 |
| `fixture-synthetic-03` | No DOI, no year; German abstract from OpenAlex; download `error`; access `unknown` |
| `fixture-synthetic-04` | One synthetic page, no abstract, no DOI; two CDE units; non-CDE first author; an `other` method with no evidence at confidence 0.3 |

## Tags

Drafted by Claude (claude-opus-5-5) in a coding session on 2026-11-06 from each
record's title, keywords, abstract and stored pages, against
`vocab/draft_vocabulary.yaml` v0.2. Every tag then has to be checked by hand in
`tag_review.csv` (`status` → `ok` / `changed` / `removed`, plus `checked_by`,
`checked_on`). `tests/test_fixtures.py::test_every_tag_checked_by_hand` fails until
that is done.

- `term` is the vocabulary code (`SL-d`, `O2`, `TA1`, `MET-case-study`,
  `SET-mountains`, ISO alpha-3). SDGs are `"1"`–`"17"`;
  country may be `global_or_multi_country`; region is a CDE partner region or a
  UN M49 (sub-)region name.
- `evidence` is a verbatim quote from the text named by `source` (pages for
  `fulltext`, the abstract, or title + keywords for `metadata`). A test checks this
  after Unicode normalisation (NFKC, soft hyphens removed, whitespace collapsed).
  76 tags have no quote, mostly derived ones (region from country, `None explicit`).
- Confidence: ≥ 0.8 stated explicitly; 0.5–0.7 inferred; < 0.5 uncertain (28 tags).
- Only 14 of the 30 real records have a BORIS abstract; tags rely on pages for the
  rest. Many first pages are front matter, so some tags are thin by design.
- `provenance.model` is `claude-opus-5-5 (session draft)`; `prompt_version` is empty.
- **Vocabulary v0.3 (2026-11-08, #14).** The v0.2 labels were remapped to codes with
  `scripts/remap_fixture_tags.py` and `vocab/curation/v0.3_codes.csv` (272 tags; review
  note "remapped to v0.3 code"; `vocab_version` 0.3 on every tag). Merged terms take
  the code they were merged into. The one tag on the retired *Futures methods*
  (`fixture-synthetic-04`) became `MET-scenario-development-and-analysis`. 22
  `additional_sub_area` tags were drafted by Claude the same day, with verbatim quotes,
  and checked by hand.
Tags per dimension: method 66, actors 57, sdg 55, sub_area 52, country 52,
impact_area 44, system 42, justice_lens 39, output_type 33, region 31,
research_mode 29, objective 28, data_type 28, scale 25, additional_sub_area 22,
pathway 22, setting 19, transformative_aspiration 10.

## Rebuilding

```bash
pip install -e '.[dev,fixtures]'
python scripts/select_fixture_records.py           # selection.csv (no network)
python scripts/fetch_fixture_records.py            # raw/ (UniBe VPN; resumable)
python scripts/fetch_fixture_records.py --reextract  # pages.json from local PDFs, no network
python scripts/build_fixture_jsonl.py              # records/authors/pages/downloads.jsonl
python tests/fixtures/build_fixture_db.py          # fixture.duckdb (optional; tests build their own)
pytest tests/test_fixtures.py
```

`tags.jsonl` and `tag_review.csv` are hand-made and not rebuilt. If a record changes,
fix its tags by hand.

## Credits and licences

All 30 real records are © their authors and are redistributed here (metadata, and
extracted text of at most 6 pages each) under the licence of their BORIS file. The
synthetic records are not real publications. Metadata from BORIS Portal, University
of Bern (https://boris-portal.unibe.ch).

| record_id | Citation | Licence |
|---|---|---|
| `0674ad8c-bb2d-48d0-b866-8a07df79f5af` | Herweg, Karl Günter; Lundsgaard-Hansen, Lara Maria; Zimmermann, Anne Barbara; et al. (2016). Nachhaltige Entwicklung in die Hochschullehre integrieren — Ein Leitfaden mit Vertiefungen für die Universität Bern. Vertiefung 3: Weiterführende Hilfen. https://doi.org/10.7892/boris.82673 | [CC-BY-NC-4.0](https://creativecommons.org/licenses/by-nc/4.0) |
| `0d741fd9-b15d-4057-b6f9-22395f89b413` | Karrer, Laurenzia Michaela; Steinböck, Camilla Maria; Trechsel, Lilian Julia; et al. (2023). IGS North-South Tracer Study 2022: Decolonizing a PhD Programme and Beyond. https://doi.org/10.48350/182165 | [CC-BY-NC-4.0](https://creativecommons.org/licenses/by-nc/4.0) |
| `0eb401b2-9ef0-4478-9abf-6667fb39a0ac` | Bader, Christoph; Bieri, Sabin (2023). A World Drifting Apart: Challenges and Possibilities of Transitioning to a More Equitable Planet. *Transitioning to Reduced Inequalities*. https://doi.org/10.48350/179334 | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0) |
| `25c17984-c6c0-4487-8638-5bb606748603` | Wymann von Dach, Susanne; Ott, Cordula; Kläy, Andreas; et al. (2006). Will International Pursuit of the Millenium Development Goals Alleviate Poverty in Mountains? *Mountain Research and Development*. https://doi.org/10.7892/boris.18890 | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0) |
| `2760e535-c973-4524-ab84-3a1c71b9bf3a` | Mathez-Stiefel, Sarah-Lan; Dubé, Monique; Humphries, Murray M.; et al. (2022). Focus Issue: Weaving Together Knowledges—Collaborations in Support of the Wellbeing of Mountain Peoples and Regions. Editorial. *Mountain Research and Development*. https://doi.org/10.48350/178836 | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0) |
| `453f1ec7-d260-4d02-a003-6b95d48c26b2` | Zähringer, Julie Gwendolin; Ramamonjisoa, Bruno; Messerli, Peter (2018). Conservation versus local livelihoods? Sustainable development challenges in Madagascar. *CDE Policy Brief*. https://doi.org/10.7892/boris.128142 | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0) |
| `5a56f17f-cbe3-4cc0-9511-ec2674dd2beb` | Hertkorn, Marie-Luise (2017). "Food that makes you strong": Implicit and explicit knowledge in the food sustainability framework. *Towards Food Sustainability Working Paper*. https://doi.org/10.7892/boris.101388 | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0) |
| `6d56cc27-fd5f-47ec-8187-d298be89be1a` | Musselli, Irene; Sonderegger, Gabi; Lannen, Anu; et al. (2024). A new generation of trade measures to support biodiversity. *CDE Policy Brief*. https://doi.org/10.48620/84799 | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0) |
| `71c077c3-13b9-4df1-bd03-cd4eb27c84de` | Mintah, Frank; Tabi Eckebil, Paule Pamela; Oberlack, Christoph; et al. (2024). Why do forests persist and re-emerge amidst tropical deforestation pressures? Archetypes of governance and impact pathways. *Forest Policy and Economics*. https://doi.org/10.48620/84842 | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0) |
| `71c3358b-579b-4c24-b627-3b8d7f37e6fd` | Schild, Kirstin; Leng, Marion; Hammer, Thomas (2019). Die Rolle von Transformativem Lernen für eine Bildung für Nachhaltige Entwicklung an der Hochschule. *Bulletin VSH-AEU*. https://doi.org/10.7892/boris.132869 | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0) |
| `7ab97dd8-2ef4-41d5-ad9f-1049a091013b` | Panagea, Ioanna; Daliakopoulos, Ioannis; Tsanis, Ioannis; et al. (2015). Evaluation of soil salinity amelioration technologies in Timpaki, Crete. *Geophysical research abstracts*. https://doi.org/10.7892/boris.74555 | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0) |
| `8001ee4f-ae98-47a8-9347-26a6da9baa21` | Moser, Stephanie; Gurtner, Lilla; Bezzola, Nicolà Renato; et al. (2024). Wohlbefinden und Ökologie durch mehr Zeit? *CDE Working Papers*. https://doi.org/10.48350/194439 | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0) |
| `82d7bbd0-6e95-44c5-9fac-617c24dd3bd2` | Scharrer, Bettina; Kubioka, Luisa Quarta (2024). Die Ernährungspolitik der Stadt Basel. Strategien, Aktivitäten, Massnahmen und Rahmenbedingungen. *Ernährungspolitik Schweizer Städte / La politique alimentaire des villes suisses*. https://doi.org/10.48350/196391 | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/) |
| `8632a01d-8dad-40aa-9926-a9120617c29a` | Kohler, Thomas; Wymann von Dach, Susanne (2015). CDE links regional research and global efforts for sustainable mountain development. *Mountain Research and Development*. https://doi.org/10.7892/boris.70122 | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0) |
| `8e47c5f7-3dc8-40d8-a993-34c2fbd62074` | Sonderegger, Gabi; Heinimann, Andreas; Diogo, Vasco; et al. (2022). Governing spillovers of agricultural land use through voluntary sustainability standards: A coverage analysis of sustainability requirements. *Earth system governance*. https://doi.org/10.48350/177656 | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0) |
| `9db04558-724d-47f2-ae90-f350fe772fee` | Hurni, Hans; Veit, Heinz; Wanner, Heinz; et al. (2003). Research on Mountains and Global Change. *Geographica Helvetica*. https://doi.org/10.7892/boris.77597 | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0) |
| `a09e8861-8eec-4055-87a1-676e546e6541` | Andriamihaja, Onintsoa Ravaka Niaina; Metz, Florence Alessa; Zähringer, Julie Gwendolin; et al. (2019). Land Competition under Telecoupling: Distant Actors’ Environmental versus Economic Claims on Land in North-Eastern Madagascar. *Sustainability*. https://doi.org/10.7892/boris.128143 | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0) |
| `aa8e1522-ccd7-4dcf-a9e6-44a5d6eae9a7` | Bracher, Christoph Peter; Wymann von Dach, Susanne; Adler, Carolina (2018). Challenges and opportunities in assessing sustainable mountain development using the UN Sustainable Development Goals. *CDE Working Papers*. https://doi.org/10.7892/boris.119737 | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0) |
| `ac8dbaec-da44-400b-aad5-1ac1d1838bb6` | Sütterlin, Bernadette; Zimmermann, Anne B.; Carabias-Hütter, Vicente; et al. (2026). Akademische Nachhaltigkeits netzwerke zwischen Exzellenz und Wirkung. Wie kann die Wissenschafts-Bubble durchlässiger werden? *GAiA - Ecological Perspectives for Science and Society*. https://doi.org/10.48620/96598 | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0) |
| `bdf7b415-f133-4632-a184-ee6e0c62517b` |  (2019). Werte in der Hochschullehre. https://doi.org/10.48350/132866 | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0) |
| `c191917c-34df-4dfe-8f2d-aaaffb20bb05` | Kubitza, Christoph; Grislain, Quentin; Rivera, NIkka; et al. (2025). Large-scale land acquisitions for carbon offsetting: Green grabbing or just transition? Analytical report on land-based offset projects. https://doi.org/10.48620/91000 | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0) |
| `c45c2a72-d5ad-43b8-a113-3dc0a267aed8` | Kläy, Andreas; Heim, Thomas; Minsch, Jürg; et al. (2011). Welche Forschung führt zu Innovationen für eine nachhaltige Entwicklung? *GAIA - ökologische Perspektiven für Wissenschaft und Gesellschaft / ecological perspectives for science and society*. https://doi.org/10.7892/boris.9071 | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0) |
| `c7c9136f-52b3-40d4-893c-cb638a9c2147` | Chastonay, Jonas Frédéric; Karrer, Laurenzia Michaela; Trechsel, Lilian Julia (2023). Monitoring-Bericht zuhanden des Vizerektorats Qualität: Integration von Nachhaltiger Entwicklung in der Lehre an der Universität Bern, akademisches Jahr 2022/2023. https://doi.org/10.48350/181782 | [CC-BY-NC-4.0](https://creativecommons.org/licenses/by-nc/4.0) |
| `d012a8d7-8b08-41fe-b46b-f551dd068417` | Scharrer, Bettina; Kubioka, Luisa Quarta; Utz Radermacher, Kathrin Margret (2024). Die Ernährungspolitik der Stadt Winterthur. Strategien, Aktivitäten, Massnahmen und Rahmenbedingungen. *Ernährungspolitik Schweizer Städte / La politique alimentaire des villes suisses*. https://doi.org/10.48350/196729 | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/) |
| `d3bf1ec2-8095-4b02-9349-e62ce449d7e2` | Carmenta, Rachel; Bastos Lima, Mairon; Choiruzzad, Shofwan A. B.; et al. (2025). Unveiling pervasive assumptions: moving beyond the poverty-biodiversity loss association in conservation. *Current Opinion in Environmental Sustainability*. https://doi.org/10.48620/88687 | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0) |
| `d74fe85e-543f-4f8e-a742-1927a8b09bd4` | Oberlack, Christoph; Pedde, Simona; Piemontese, Luigi; et al. (2023). Archetypes in support of tailoring land-use policies. *Environmental Research Letters*. https://doi.org/10.48350/186997 | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0) |
| `dac3d725-e65b-4d99-a135-1f8eec9e4f83` | Leimer, Sophie; Willimann, Elias; Alaoui, Abdallah; et al. (2015). Element export from a small catchment in the tropical montane forest of Ecuador responds to climate change. *Geophysical research abstracts*. https://doi.org/10.7892/boris.80226 | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0) |
| `e5176970-c1f2-4e83-82d7-3f7f779a1d08` | Schneider, Flurina; Homewood, Christine (2013). Exploring Water Governance Arrangements in the Swiss Alps From the Perspective of Adaptive Capacity. *Mountain Research and Development*. https://doi.org/10.7892/boris.39666 | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0) |
| `e7a43853-bb46-489a-8978-c7abf3b99585` | Wymann von Dach, Susanne; Mathez-Stiefel, Sarah-Lan; Thibault, Marlène; et al. (2022). Editorial. *Mountain Research and Development*. https://doi.org/10.48350/178832 | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0) |
| `f50a5dcd-3422-45f4-b579-4abef4dda308` | Hammer, Thomas; Leng, Marion; Scharrer, Bettina (2020). Nachhaltige Regional- und Landschaftsentwicklung in integrativen Grossschutzgebieten: Welche Rolle soll der Kultur beigemessen werden? *CDE Working Papers*. https://doi.org/10.48620/8446 | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0) |
