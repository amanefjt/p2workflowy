import re

# V2.9.4 regex
_RUNNING_HEADER_RE = re.compile(
    r'^.{3,60}\s[\u00b7\u2022]\s(?:\d{1,4}|[ivxlcdmIVXLCDM]{1,8})$'
    r'|^(?:\d{1,4}|[ivxlcdmIVXLCDM]{1,8})\s[\u00b7\u2022]\s.{3,60}$'
    r'|^.{10,55}\s\d{1,3}$'
)

test_cases = [
    "Chapter 1 The Ethnographic Effect I 1",
    "Chapter 2 Pre-figured Features 29",
    "Chapter 3 The Aesthetics of Substance 45",
    "Chapter 4 Refusing Information 64",
    "Chapter 5 New Economic Forms: a Report 89",
    "Chapter 6 The New Modernities 117",
    "Chapter 7 Divisions of Interest and Languages of Ownership 138",
    "Chapter 8 Potential Property: Intellectual Rights and Property in Persons 161"
]

for t in test_cases:
    match = _RUNNING_HEADER_RE.match(t)
    print(f"'{t}' (len={len(t)}) -> {'MATCH (REMOVED)' if match else 'NO MATCH (SAVED)'}")

# Proposed fix: Add negative lookahead for Chapter/Part
_RUNNING_HEADER_RE_FIXED = re.compile(
    r'^.{3,60}\s[\u00b7\u2022]\s(?:\d{1,4}|[ivxlcdmIVXLCDM]{1,8})$'
    r'|^(?:\d{1,4}|[ivxlcdmIVXLCDM]{1,8})\s[\u00b7\u2022]\s.{3,60}$'
    r'|^(?!(?:Chapter|Part|Contents|Preface|Notes|Bibliography|Index)\b).{10,55}\s\d{1,3}$'
)

print("\n--- Fixed Regex Test ---")
for t in test_cases:
    match = _RUNNING_HEADER_RE_FIXED.match(t)
    print(f"'{t}' -> {'MATCH (REMOVED)' if match else 'NO MATCH (SAVED)'}")
