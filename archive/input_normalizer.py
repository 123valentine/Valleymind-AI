from textblob import TextBlob
from valleymind_utils.abbreviations import ABBREVIATIONS
from valleymind_utils.synonyms import SYNONYMS
from valleymind_utils.fuzzy_corrections import FUZZY_CORRECTIONS

def normalize_input(prompt: str) -> str:
    corrected = str(TextBlob(prompt)).lower()

    # Step 1: Apply fuzzy corrections
    for wrong, right in FUZZY_CORRECTIONS.items():
        corrected = corrected.replace(wrong, right)

    # Step 2: Expand abbreviations
    for abbr, full in ABBREVIATIONS.items():
        corrected = corrected.replace(abbr, full)

    # Step 3: Replace synonyms
    for word, synonym in SYNONYMS.items():
        corrected = corrected.replace(word, synonym)

    return corrected.strip()