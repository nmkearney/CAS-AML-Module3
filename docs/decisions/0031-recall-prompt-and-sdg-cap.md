# 0031: A looser tagging prompt (tag.v5) and up to five SDGs

- Status: accepted (pilot `pilot_v5` spot-checked; full run `main` with tag.v5, 2026-11-10)
- Date: 2026-11-09

## Context
Pilot 3 (tag.v3) is precise but misses tags. Decision 0029 gives the numbers:
- **Precision:** 89% (98.5% without the unsure tags).
- **Recall:** at most 79% at term level. gpt-oss-120b found 87 real tags that qwen
  does not count.
- **Where the misses are:**
  - mostly on full-text records (49 of 87 on 7 records);
  - qwen gives only 1-2 tags per dimension and record;
  - the per-dimension limits are rarely reached, except SDG (limit 3, reached on
    11 of 100 records). Four of the five tags the curator listed as missing are SDGs.
- **The likely cause is the prompt.** Since tag.v2 it said:
  - "Tag only the main things. Most publications have 1-2 sub-areas, 1-3 methods and
    no additional sub-area at all."
  - "If you would choose `weak`, consider leaving the tag out."
- **Alternatives rejected:**
  - One batch per dimension: 5× the requests (about 33 hours for the corpus), no sign
    that list length causes the misses, and the sub-area / additional sub-area rule
    needs both lists in one request.
  - Running gpt-oss as well and counting the union of both models: a second full run,
    and 11% of gpt-oss's extra tags are wrong.

## Decision
- **Prompt tag.v5** (from tag.v4):
  - The general rule says to tag every term the work substantially addresses, not
    only the main one, each with its own quote, and to leave out passing mentions.
  - The "consider leaving weak tags out" line is dropped. Weak tags stay below 0.5,
    so they never count.
  - Per-dimension wording:
    - sub_area, system, scale: every one, not the closest or main one;
    - additional_sub_area: "many publications need none" instead of "most";
    - method: all methods, data collection and analysis alike.
  - justice_lens names what each lens looks like without the word "justice", since
    it had the lowest recall (64%).
  - SDGs: up to five, every goal the content clearly relates to.
- **The SDG cap is raised from 3 to 5** (`tagging.max_per_dimension.sdg`,
  `vocab: sdg.max_per_record`). The cap is not part of the shared formats
  (`schemas.py`).
- **The v4 full run was stopped** after 274 of 9,009 requests, to test v5 on the
  pilot first. The v4 rule (researchers count as co-producers only outside the author
  team) is kept in v5.
- **How v5 is judged:**
  - `pilot_v5` on the same 100 records.
  - The review sheet carries decisions from both earlier workbooks
    (`--carry-from review_pilot3.xlsx --also-from recall_alt.xlsx`), so the curator
    judges only tags nobody has judged yet.
  - v5 is adopted if recall rises clearly and precision stays near pilot 3.

## Results (pilot_v5, before the curator's judgement)
- **Run:** 300 requests, 0 failures, 1% of quotes not found.
- **More tags, mostly where recall was low:**
  - system 195 → 262, sub_area 152 → 198, sdg 152 → 198;
  - additional_sub_area 133 → 180, actors 168 → 196.
- **Counted tags on the 20 reviewed records:** 420 (pilot 3: 361).
  - Of those already judged: 316 ok, 5 wrong, 32 unsure (90% ok; 98.4% without the
    unsure tags). Pilot 3: 89% and 98.8%.
  - 67 have not been judged yet.
- **v5 swaps as well as adds:**
  - 93 tags counted that pilot 3 did not;
  - 34 pilot 3 tags no longer counted, 27 of them judged ok.
- **Recall against the 440 known-ok tags:** 72% (pilot 3: 73%).
- **The 68 new counted tags decide:** at about 85% ok, recall would rise to about 75%
  against pilot 3's 65% (out of a larger set of known-ok tags).
- The curator judges them in `data/reference/review_pilot_v5.xlsx`.

## Consequences
- More tags per record: counts rise, and gap answers are compared within one prompt
  version only.
- The SDG question in `schema_draft.md` is settled.
