"""Transparency label generation (planning.md Section 5).

Maps the combined confidence score + attribution to the exact reader-facing label text.
The numeric percentage is embedded in every variant so the displayed figure changes
continuously even when the label category does not.
"""


def _pct(score):
    return f"{round(score * 100)}%"


def make_label(attribution, combined_score):
    score = _pct(combined_score)

    if attribution == "likely_ai":
        return (
            f"This content is likely AI-generated ({score} confidence). "
            "Our detection system found strong indicators of machine authorship."
        )

    if attribution == "likely_human":
        human = _pct(1 - combined_score)
        return (
            f"This content is likely human-written ({score} confidence that it is "
            f"AI-generated, i.e. {human} likely human). Our detection system found "
            "few indicators of machine authorship."
        )

    # uncertain
    return (
        "We could not confidently determine whether this content is AI-generated or "
        f"human-written ({score} confidence it is AI-generated). Treat this attribution "
        "with caution."
    )
