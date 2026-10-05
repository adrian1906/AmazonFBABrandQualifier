"""
Display-only label unification between the two different recommendation
vocabularies this project uses internally:

  Brand Qualifier (Stage 1):       PURSUE / INVESTIGATE / HOLD / REJECT
  Distributor Qualifier (Stage 2): CONTACT_NOW / INVESTIGATE_FURTHER / DO_NOT_PURSUE

These aren't different concepts at the top and middle tiers - CONTACT_NOW
means the same thing PURSUE does (ready to act now, no further research
needed) and INVESTIGATE_FURTHER means the same thing INVESTIGATE does
(worth a closer look before deciding). They're just worded differently
because each stage's qualification agent was written independently. Seeing
four different words for two concepts side by side - e.g. across a brand
batch review and a distributor batch review open at once - is confusing,
so printed reports go through display_recommendation() below instead of
printing the raw value directly.

This only changes what gets PRINTED. The underlying field on
QualificationResult/SupplierAssessment is untouched, and so is every
comparison against it (e.g. "if recommendation == 'CONTACT_NOW'") - those
still use the real enum values from models.py.
"""

_DISPLAY_LABELS = {
    "PURSUE": "Pursue",
    "CONTACT_NOW": "Pursue",
    "INVESTIGATE": "Investigate",
    "INVESTIGATE_FURTHER": "Investigate",
    "HOLD": "Hold",
    "REJECT": "Reject",
    "DO_NOT_PURSUE": "Do Not Pursue",
}


def display_recommendation(raw: str) -> str:
    """The unified display label for a raw recommendation value from either
    stage's model - see module docstring. Falls back to the raw value itself
    for anything unrecognized, so a future enum addition doesn't silently
    vanish from a report instead of just showing up unmapped."""
    return _DISPLAY_LABELS.get(raw, raw)
