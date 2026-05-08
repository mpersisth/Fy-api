"""Quality evaluation for Fy-api channels.

Runs a JSONL golden-prompt suite against each configured channel, grades
outputs with a mix of deterministic assertions and LLM-as-judge rubrics,
and emits a per-channel scorecard (markdown + JSON).

See README.md in scripts/channel-benchmark/py/ for the full story.
"""

__version__ = "0.2.0"
