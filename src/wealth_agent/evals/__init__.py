"""Evaluation: the outer loop, and the loop that checks the loop.

Three things live here, in increasing order of how rarely teams build them:

* :mod:`~wealth_agent.evals.evaluators` — scorers for an experiment.
  Deterministic first, LLM judge second, trajectory third. Ordering by cost is
  not an optimization; it is a correctness argument. A deterministic check that
  can settle a question should settle it, because it will give the same answer
  next week.
* :mod:`~wealth_agent.evals.fixtures` — memos with known defects and human
  labels. The substrate for judge alignment.
* :mod:`~wealth_agent.evals.judge_alignment` — measures the judge against those
  labels. **This is the part almost nobody builds**, and skipping it means every
  number the judge produces is uncalibrated. You would not ship a metric from an
  unvalidated sensor; an LLM judge is a sensor.
"""
