import re

_RUNNING_HEADER_RE = re.compile(
    r'^.{3,60}\s[\u00b7\u2022]\s(?:\d{1,4}|[ivxlcdmIVXLCDM]{1,8})$'   # 中黒区切り（前テキスト）
    r'|^(?:\d{1,4}|[ivxlcdmIVXLCDM]{1,8})\s[\u00b7\u2022]\s.{3,60}$'  # 中黒区切り（前ページ番号）
    r'|^.{10,55}\s\d{1,3}$'   # [V2.9.4 追加] "Title 233"形式
)

test_cases = [
    "Chapter 1 The Ethnographic Effect I 1",
    "Part I EFFECTS",
    "Chapter 2 Pre-figured Features 29",
    "Chapter 5 New Economic Forms: a Report 89",
    "Chapter 7 Divisions of Interest and Languages of Ownership 138",
    "Chapter 8 Potential Property: Intellectual Rights and Property in Persons 161",
    "Notes 262"
]

for t in test_cases:
    match = _RUNNING_HEADER_RE.match(t)
    print(f"'{t}' -> {'MATCH' if match else 'NO MATCH'}")
