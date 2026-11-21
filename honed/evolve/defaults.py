"""The default reflection schema of a round: lessons from a session, with the configuration lines rated on usefulness
and, when the session shows one, the failure mode they contributed to (``honed.taxonomy``: the modes the session
analysis finds too).

The recipe (``evolve.py`` in the evolve skill) imports these; a recipe may declare its own instead.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from honed.resources import CategoricalScale, Ref
from honed.taxonomy import TAXONOMY

USEFULNESS = CategoricalScale(
    "usefulness",
    {
        "harmful": "the cited lines made the agent's work worse",
        "neutral": "evidence shows the cited lines were encountered or followed but made no material difference",
        "helpful": "the cited lines demonstrably contributed to progress, correctness, efficiency, or safe handling",
    },
    values={"harmful": -1, "neutral": 0, "helpful": 1},  # Order, and which side of neutral: the report diverges.
    description="Evidence-based effect of cited configuration lines; absence of evidence does not imply harm.",
)

FAILURE_MODES = CategoricalScale(
    "failure_mode",
    {mode.id: mode.definition for mode in TAXONOMY},
    description="The failure mode the cited lines contributed to (or failed to prevent), when the session shows one; "
    "leave it empty when no failure mode is evidenced.",
)


class Rated(BaseModel):
    """One judgment of cited configuration lines."""

    ref: Ref
    usefulness: Annotated[str, USEFULNESS]
    failure_mode: Annotated[str, FAILURE_MODES] | None = Field(
        default=None,
        description="Only for lines that contributed to a failure the session shows, or failed to prevent it: which "
        "failure mode. Leave it null otherwise.",
    )


class Lesson(BaseModel):
    """What the session teaches about the configuration."""

    reasoning: str = Field(min_length=1, description="Concise evidence summary supporting the conclusions; do not provide private chain-of-thought.")
    error_identification: str = Field(min_length=1, description="Observed outcome or difficulty, including when the task succeeded.")
    root_cause_analysis: str = Field(
        min_length=1, description="Evidence-supported cause, or state that it is unclear; distinguish agent lapse from configuration gap."
    )
    correct_approach: str = Field(min_length=1, description="A concrete reusable behavior that would address the supported cause, if one exists.")
    key_insight: str = Field(
        min_length=1, description="Transferable lesson for future work; avoid a one-off answer or domain-specific fact."
    )
    ratings: list[Rated] = Field(description="The configuration lines that shaped the behaviour, each rated.")


SCHEMA = Lesson  # Anything that takes a schema takes the model itself.
