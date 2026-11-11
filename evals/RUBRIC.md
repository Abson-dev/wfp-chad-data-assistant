# Expert grading of the assistant

The automatic grading of `run_evals.py` checks a figure, a phrase and a tool call. It cannot tell whether an answer is right in substance, deep enough or free of speculation. For that, experts grade the answers, following the method IFPRI used to evaluate RIAPA-AI, its assistant over a CGE model (Mukashov et al., 2026, *An agentic AI assistant for country-level economic modeling*, IFPRI Discussion Paper 2424, https://hdl.handle.net/10568/183521).

## What is compared

- **The agent**: the assistant, with its instructions and tools.
- **The baseline**: the same model alone, with no instructions, no tools and no data. It gets the question with one line of context, as a user would type it into a general-purpose chat. RIAPA-AI's evaluation found that such a model gets the direction of a change right but invents numbers and explanations. The baseline measures what the tools and the instructions add.

`python evals/run_evals.py --baseline --sheet` asks both and writes two files:

- `grading_sheet_<time>.csv`: one row per answer, in random order, labelled by an item number only, so the graders do not know which system wrote it;
- `grading_key_<time>.csv`: which system wrote each item. Keep it from the graders until they have finished.

This costs about twice a normal eval run. Ask before running it.

## The three dimensions

Each answer gets a score of 1, 0.5 or 0 on each dimension. Leave the cell blank when a dimension does not apply. For example, depth does not apply to a yes-or-no question on the merge.

| Dimension | What it asks, for these data |
|---|---|
| **Correctness** | Does the answer read the question right (indicator, rounds, series, region) and give the right figures? Does it report the flags and data issues that bear on them? Does it refuse what the data cannot give, such as household records or a cause? |
| **Depth** | Does it go beyond the headline? For example: where a change happened (the regions that improved and worsened), lean season against post-harvest, the sample and the regions covered in each round, the merge path of a variable, the rules and caveats of a cost. |
| **Objectivity** | Does every statement rest on the data? It must not attribute a change to rainfall, prices, conflict or the programme, give no policy advice, and invent no numbers. It must state the limits: unweighted figures, no panel, no significance test. |

The scores mean:

- **1, robust:** sound, complete and usable as it stands for the analysis or a report.
- **0.5, incomplete:** right on the main point, but a caveat, a secondary effect or a needed detail is missing.
- **0, fail:** a wrong figure or direction, an invented number or cause, or a reading that would mislead.

**How the experts agree on a score.** RIAPA-AI's evaluation used three senior economists who agreed on every score together. Here, at least two people should grade, for example the IFPRI analyst and a WFP colleague. They grade each answer separately, then agree on one score. Note any disagreement in the `comment` column.

## Scoring

```
python evals/score_rubric.py evals/results/grading_sheet_<time>.csv evals/results/grading_key_<time>.csv
```

For each system and dimension, the script gives:
- the share of answers scored 1, with its exact 95% interval;
- for the questions graded for both systems, two tests:
  - McNemar's exact test, on how often each system scores 1;
  - the Wilcoxon signed-rank test, on the full scores.

With about 25 questions these tests have little power. Read the shares, and above all the answers that scored 0.

## When to grade again

- After a change of model, `WFP_AGENT_MODEL`. The results file records the model, because a model can behave differently from one version to the next.
- After a change of the instructions or the tools.
- After a new version of the merged file.
