# Reference papers

Primary-source PDFs so claims in the `notebooks/results*.ipynb` set can be cited and quoted without relying on memory.

Every title, author list, version date and abstract below was read off `https://arxiv.org/abs/<id>`
and the arXiv Atom API on **2026-08-07**. Per-paper machine-readable metadata is in `<id>.json`
alongside each PDF. Affiliations are the one field that could **not** come from arXiv — abstract
pages do not carry them — so those were read off each PDF's title page and are labelled as such in
the JSON.

The PDFs are gitignored (`papers/*.pdf`, one line appended to the repo `.gitignore`): they are ~10 MB
of third-party binaries, re-fetchable from the arXiv ids recorded here, and this repo already
gitignores large re-derivable artifacts for the same reason. The `.json` files and this README are
the citation source of truth and **are** committed.

---

## 1. Control Tax: The Price of Keeping AI in Check

Mikhail Terekhov, Zhen Ning David Liu, Caglar Gulcehre, Samuel Albanie.
arXiv:2506.05296 (v1 5 Jun 2025, v2 14 Jun 2025, **v3 2 Mar 2026**). ICLR 2026 — *see caveat*.
EPFL (CLAIRE lab) and MATS. Code: <https://github.com/MikhailTerekhov/control-tax-code> (MIT).

- Local file: `2506.05296-control-tax.pdf` (48 pp.)
- Metadata: `2506.05296.json`

**Relevance.** It is the cost model this project's monitor has to price against: it converts
monitor ROC performance into safety guarantees under an auditing budget and attaches dollar costs,
which is exactly the axis on which an execution-grounded PBT monitor (cheap CPU, no monitor tokens)
should beat a text monitor. Its released APPS backdoor dataset is also directly reusable — see the
repo note at the end.

**Believed vs. verified.** Believed values were all correct. Author 2's full name is
"Zhen Ning David Liu".

---

## 2. AI Control: Improving Safety Despite Intentional Subversion

Ryan Greenblatt, Buck Shlegeris, Kshitij Sachan, Fabien Roger. Redwood Research.
**arXiv:2312.06942** (v1 12 Dec 2023 … v5 23 Jul 2024).
Published in *Proceedings of the 41st International Conference on Machine Learning* (ICML 2024).

- Local file: `2312.06942-ai-control-intentional-subversion.pdf` (47 pp.)
- Metadata: `2312.06942.json`

**Relevance.** The paper that defines the control evaluation, the blue-team/red-team framing, the
trusted-monitoring baseline and the APPS backdoor setting that this whole repository is an
intervention on.

**Believed vs. verified.** Title and authors correct. The arXiv id was not supplied and was found by
title search: **2312.06942**. Venue is confirmed two ways (arXiv comment field points at
`openreview.net/pdf?id=KviM5k8pcP`; the PDF footer reads "Proceedings of the 41 st International
Conference on Machine Learning").

---

## 3. Adaptive Attacks on Trusted Monitors Subvert AI Control Protocols

Mikhail Terekhov, Alexander Panfilov, Daniil Dzenhaliou, Caglar Gulcehre, Maksym Andriushchenko,
Ameya Prabhu, Jonas Geiping. MATS; EPFL; ELLIS Institute Tübingen & MPI for Intelligent Systems;
Tübingen AI Center; University of Tübingen.
arXiv:2510.09462 (v1 10 Oct 2025, **v2 2 Mar 2026**). ICLR 2026 — *see caveat*.

- Local file: `2510.09462-adaptive-attacks-trusted-monitors.pdf` (52 pp.)
- Metadata: `2510.09462.json`

**Relevance.** This is the motivating threat for the project: it evades diverse LLM trusted monitors
with prompt injections embedded in the untrusted model's own output. An execution-grounded monitor is
structurally immune to that attack class, which is the claim the repo README opens with.

**Believed vs. verified.** Id and title correct. "Terekhov et al." is seven authors, not four; the
full list is above and in the JSON.

---

## 4. Combining Cost-Constrained Runtime Monitors for AI Safety

Tim Tian Hua, James Baskerville, Henri Lemoine, Mia Hopman, Aryan Bhatt, Tyler Tracy.
MARS; Redwood Research. arXiv:2507.15886 (v1 19 Jul 2025 … **v4 21 Oct 2025**).
Published at **NeurIPS 2025** (main conference proceedings).

- Local file: `2507.15886-cost-constrained-runtime-monitors.pdf` (19 pp.)
- Metadata: `2507.15886.json`

**Relevance.** The reference treatment of combining several monitors under a budget, which is what
the `tm_plus_pbt` and `tm_plus_pbt_ceiling` arms are; it is also the right frame for the finding that
PBT and TM should be scored on orthogonal budgets rather than one shared audit budget.

**Believed vs. verified.** Id, title and all six authors correct (first author's full name is "Tim
Tian Hua"). Venue is **not** recorded on arXiv — no journal-ref, no comment — but a camera-ready is
served from the official NeurIPS proceedings at
`papers.nips.cc/paper_files/paper/2025/file/ed56a654ddb681841fdb2184463c253e-Paper-Conference.pdf`,
which we fetched (HTTP 200) and whose first page matches this exact title and author list.

---

## 5. Basic Legibility Protocols Improve Trusted Monitoring

Ashwin Sreevatsa\*, Sebastian Prasanna\*, Cody Rushing (\* equal contribution).
Cambridge Boston Alignment Initiative (CBAI) Summer Research Fellowship; Redwood Research.
**arXiv:2602.10153**, v1 only, 9 Feb 2026. **arXiv preprint — no venue established.**

- Local file: `2602.10153-basic-legibility-protocols.pdf` (28 pp.)
- Metadata: `2602.10153.json`

**Relevance.** Closest prior work to the `pbt_informed_tm` arm: it also gives the trusted monitor
extra structure about the untrusted model's output (comments/legibility rather than execution
evidence) in the same APPS backdoor setting, and reports the same asymmetry we rely on — the extra
context disproportionately exonerates honest code, because backdoored code lacks an easy
justification.

**Believed vs. verified.** Id, title and all three authors correct. Note for citation: the abstract
printed in the PDF is **not** word-for-word the abstract on the arXiv abstract page (the PDF version
is longer and enumerates the constrain-how-code-is-written protocols); `2602.10153.json` stores the
arXiv abstract-page text. Quote from the PDF body, not from the JSON abstract, if quoting the paper
itself.

---

## Caveat on the two "ICLR 2026" attributions

Neither arXiv record for 2506.05296 or 2510.09462 carries a journal-ref, DOI or comment, so arXiv
does **not** confirm a venue for either. Both are listed as ICLR 2026 papers on EPFL's official
"EPFL @ ICLR 2026" page (<https://actu.epfl.ch/news/epfl-iclr-2026/>), and both posted a new arXiv
version on the same day (2 Mar 2026), consistent with a camera-ready. For 2510.09462 there is a
second signal: the OpenReview PDF at `openreview.net/pdf?id=wSs1Ez3aKl` is indexed with the header
"Published as a conference paper at ICLR 2026" — but a direct fetch of OpenReview returned HTTP 403
(bot check), so that header was not read first-hand. The Control Tax project page still self-cites as
`arXiv preprint`. Treat both as **likely ICLR 2026, verified only from secondary sources**; if a
citation must be exact, cite arXiv.

## What could not be verified

- **Affiliations** for every paper. arXiv abstract pages do not carry them, so they were taken from
  each PDF's title page rather than from arXiv, and are labelled `affiliations_source` in the JSON.
- **Venue** for 2506.05296 and 2510.09462 — secondary sources only, as above.
- **Venue** for 2602.10153 — none found anywhere; treat as a preprint.
- The **record count** of the Control Tax backdoor dataset (below). Counting it exactly would mean
  downloading all 34.7 MB, which we deliberately did not do.

---

## Control Tax code repository (checked, not cloned)

<https://github.com/MikhailTerekhov/control-tax-code> — **reachable**, public.

- **Licence: MIT.** Confirmed by reading `LICENSE` directly:
  "MIT License / Copyright (c) 2025 Mikhail Terekhov, Zhen Ning David Liu".
- **`data/backdoor_code_full.json` is present**, and is **34,718,317 bytes (~34.7 MB / 33.1 MiB)** —
  measured with an HTTP range request against `raw.githubusercontent.com`, not by downloading it.
- Sibling files in `data/`: `apps_are_solutions_correct.json`,
  `apps_problems_deterministic_or_not.json`.

**Schema, read from the first ~6 KB of the file.** It is a JSON array of records with fields:
`model`, `difficulty`, `sample_id`, `problem_statement`, `correct_code`, `backdoored_code`,
`score_result` (e.g. `"success"`), `hint`, a list of test cases each with `input` /
`expected_output`, and — the reusable part — **`fail_input`**, the ground-truth trigger. So the
"each backdoor comes with a ground-truth trigger input" claim is **confirmed**; the field is called
`fail_input`.

**"Thousands" is plausible but not counted.** The paper reports filtering the APPS test split down to
**2427 samples** and running many attacker models over them (Table 1 lists the model set), and 34.7 MB
at roughly 8–15 KB per record implies a few thousand rows. We did not download the file to count, so
treat the exact number as unverified.

**One discrepancy worth knowing before you use it.** The repo README says "The full dataset of
backdoored code used in the paper is located at `data/backdoor_code_full.pkl`" — a **pickle**. No
`.pkl` exists in `data/` on `main`; only the `.json`. Repo code that hardcodes the `.pkl` path will
need adjusting.
