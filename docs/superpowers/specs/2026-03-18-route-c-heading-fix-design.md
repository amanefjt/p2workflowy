# Route C Heading Demotion (Fragmentation Fix) Design

## 1. Problem Statement
In **Route C (Full VLM Mode)**, the Gemini Vision-Language Model is prompted to structure text by appending `# ` to "Chapters" and `## ` to "Sections" based on visual hierarchy. However, VLM lacks global document context. Consequently, when viewing a page where a prominent subsection (e.g., "BORROWING CATEGORIES") dominates, the VLM mistakenly categorizes it as a top-level chapter (`# `).

This results in "fragmentation" downstream (Phase 4), where these subsections are treated as entirely independent chapters, breaking the logical structure of the output Markdown file (`_p2.md`).

## 2. Constraints & Background
- **No TOC in Route C:** Unlike Book Mode (Route B), Route C does not generate or utilize a Table of Contents (`phase3_toc.json`). Relying on TOC validation is impossible without architectural changes.
- **Cost of LLM Post-Processing:** Sending the extracted structure to an LLM for hierarchy validation is too slow, expensive, and undermines the pipeline's stability.
- **Heuristic Prefix Approach:** The most deterministic approach is to enforce a rule based on the semantic properties of legitimate chapter titles.

## 3. Proposed Solution
Implement a **Heuristic Prefix Matching (Demotion Logic)** within the Phase 3 structuring logic (`phase3_structure.py`).

When parsing the VLM Markdown output (`structure_nodes_by_markdown`):
1. Identify any node marked as an `h2` (`# `).
2. Check if the node's title starts with one of the predefined "Chapter-indicating prefixes" (e.g., `Chapter`, `Part`, `Preface`, `Introduction`, `Conclusion`, `Bibliography`).
3. If the title **lacks** a valid prefix, demote the node to an `h3` (`## `) and append it as a child to the current active `h2` node. (If no `h2` exists yet, fall back to the `[Unlabeled Section]`).

## 4. Components & Modifications

### A. Prefix Dictionary
Define a constant tuple of typical English book chapter prefixes at the top of the file or within the parsing function:
```python
VALID_CHAPTER_PREFIXES = (
    "chapter", "part", "preface", "foreword", "introduction", 
    "conclusion", "bibliography", "references", "index", "appendix", "notes"
)
```

### B. `structure_nodes_by_markdown` Modification
Update the local parsing loop to reflect the demotion logic.
Needs to accept `is_book` flag to apply this strict logic primarily for books (though Route C is mostly for books now).

```python
# Pseudo-code update for h2 matching
if re.match(r'^#\s+', raw_text) and not re.match(r'^##', raw_text):
    title = re.sub(r'^#+\s+', '', raw_text).strip()
    
    # Demotion heuristic: If it doesn't sound like a chapter, it's probably a section
    is_valid_chapter = title.lower().startswith(VALID_CHAPTER_PREFIXES)
    
    if is_book and not is_valid_chapter:
        # Demote to h3
        node = TreeNode(id=chunk.id, text=title, role="h3", ...)
        # ... attach to current_h2 ...
    else:
        # Keep as h2
        node = TreeNode(id=chunk.id, text=title, role="h2", ...)
        # ... register as new chapter ...
```

### C. Update skill documentation
Update `.agent/skills/v3/SKILL.md` to document this design decision (Route C fragmentation is mitigated by prefix-based demotion).

## 5. Alternatives Considered
*   **LLM Verification:** Rejected to maintain speed and reduce token costs.
*   **TOC Extraction for Route C:** Rejected as it requires multi-pass VLM extraction which defeats the purpose of the single-pass Route C design.

## 6. Success Criteria
Running a Route C extraction on the target PDF (`psdpdf.pdf`) will no longer render "BORROWING CATEGORIES" (or similar subsections in Chapter 8) as top-level `h2` elements, but instead as `h3` elements correctly nested under Chapter 8.
