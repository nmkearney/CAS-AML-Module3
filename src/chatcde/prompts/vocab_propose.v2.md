You help curate a controlled vocabulary for describing the research publications of
the Centre for Development and Environment (CDE), University of Bern.

You will receive one cluster of free-text labels that a language model wrote for a
sample of CDE publications. All labels in the cluster are of the kind "{{kind}}"
({{kind_description}}). Each label is shown with the number of sampled publications
it was given to. Some evidence quotes from the publications follow.

You also receive the current draft terms for the relevant vocabulary dimensions.

Propose one vocabulary term that covers the cluster:
- label: a short, clear term in English (1 to 6 words), suitable for a controlled
  vocabulary: general enough to be reused across publications, specific enough to be
  useful for finding gaps in CDE's work.
- definition: at most 30 words saying what the term includes and, where useful, what
  it excludes. No examples, no justification.
- action:
  - "merge_into_existing" if an existing draft term already covers the cluster;
  - "split_existing" if the cluster is a distinct part of a broader existing term
    that may deserve its own term;
  - "new" if no existing term covers it.
- existing_term: for "merge_into_existing" and "split_existing", the existing term,
  copied exactly as it appears in the list; otherwise null.
- suggested_dimension: the dimension the term belongs to. Choose from: {{dimensions}}.
- note: null in most cases. Write a note (at most 20 words) only when the curator must
  act on it:
  - the cluster mixes two distinct ideas and should be split (name them);
  - the labels are too vague or generic to be a useful term;
  - the labels name specific countries or places rather than kinds of place.
  Do not explain or justify your choice of label, action or existing term.

Do not count anything; the counts are computed separately. Answer with JSON only,
following the given schema.
