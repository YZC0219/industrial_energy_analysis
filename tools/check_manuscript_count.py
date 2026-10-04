from pathlib import Path
import re

from docx import Document


ROOT = Path(__file__).resolve().parents[1]
ARCHIVED_MANUSCRIPT = ROOT.parent / "industrial_energy_analysis_archive" / "manuscripts" / "IEJ_industrial_energy_forecasting_manuscript.docx"
MANUSCRIPT = (
    ARCHIVED_MANUSCRIPT
    if ARCHIVED_MANUSCRIPT.exists()
    else ROOT / "IEJ_industrial_energy_forecasting_manuscript.docx"
)


def count_words(paragraphs):
    return sum(len(re.findall(r"\b[\w−'-]+\b", paragraph)) for paragraph in paragraphs)


paragraphs = [paragraph.text for paragraph in Document(MANUSCRIPT).paragraphs]
references_index = next(
    (
        index
        for index, paragraph in enumerate(paragraphs)
        if paragraph.strip().casefold() == "references"
    ),
    None,
)
if references_index is None:
    raise ValueError(f"'References' heading was not found in {MANUSCRIPT}")

print(
    "before_references",
    count_words(paragraphs[:references_index]),
    "references_to_end",
    count_words(paragraphs[references_index:]),
    "total",
    count_words(paragraphs),
)
