# Route C Heading Demotion (Fragmentation Fix) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a heuristic prefix matching logic to demote falsely identified 'Chapters' (h2) into 'Sections' (h3) during Phase 3 structuring in Route C, fixing the fragmentation bug in Book Mode.

**Architecture:** Update `structure_nodes_by_markdown` in `core/phase3_structure.py` to identify missing chapter prefixes (like 'Chapter', 'Part') in `h2` Markdown outputs and convert them to `h3` nodes automatically. 

**Tech Stack:** Python, Regex.

---

### Task 1: Update `phase3_structure.py` Node Parsing

**Files:**
- Modify: `core/phase3_structure.py`

- [ ] **Step 1: Add Prefix Constants and update function signature**

At the top of `structure_nodes_by_markdown` or just outside it, define the valid prefixes. Update the function signature to accept `is_book: bool = False`.

```python
VALID_CHAPTER_PREFIXES = (
    "chapter", "part", "preface", "foreword", "introduction", 
    "conclusion", "bibliography", "references", "index", "appendix", "notes",
    "acknowledgements", "afterword"
)

def structure_nodes_by_markdown(
    chunks: List[RawChunk],
    is_book: bool = False,
) -> tuple[List[TreeNode], Dict[str, List[dict]]]:
```

- [ ] **Step 2: Implement the demotion logic inside the parsing loop**

Locate the `h2` parsing block (`if re.match(r'^#\s+', raw_text)...`). Inject the verification logic.

```python
        # --- トップレベル見出し（章: h2）---
        if re.match(r'^#\s+', raw_text) and not re.match(r'^##', raw_text):
            title = re.sub(r'^#+\s+', '', raw_text).strip()
            
            # --- Demotion Logic for Route C ---
            is_valid_chapter = title.lower().startswith(VALID_CHAPTER_PREFIXES)
            
            if is_book and not is_valid_chapter:
                # Demote to h3 (Section)
                node = TreeNode(
                    id=chunk.id, text=title, role="h3", seq_index=chunk.seq_index, children=[]
                )
                if current_h2 is not None:
                    current_h2.children.append(node)
                else:
                    tree.append(node)
                
                sections_dict.setdefault(current_section_key, []).append(
                    {"id": node.id, "text": node.text, "role": "h3"}
                )
                current_h3 = node
                continue # Skip the normal h2 processing
            
            # Normal h2 processing
            node = TreeNode(
                id=chunk.id, text=title, role="h2", seq_index=chunk.seq_index, children=[]
            )
            tree.append(node)
            current_section_key = f"{chunk.id}|{title}"
            sections_dict[current_section_key] = []
            current_h2 = node
            current_h3 = None
```

- [ ] **Step 3: Update `run_phase3` caller**

In `run_phase3`, pass the `is_book` argument to `structure_nodes_by_markdown`.

```python
    if pdf_mode == "full_vlm":
        print_log("  [Phase 3] Route C: VLM Markdown Mode (正規表現パース)")
        chunks = load_chunks_from_json(str(phase1_state_path))
        tree, sections_dict = structure_nodes_by_markdown(chunks, is_book=is_book)
```

- [ ] **Step 4: Commit changes**

```bash
git add core/phase3_structure.py
git commit -m "fix: demote falsely identified h2 chapters to h3 in Route C based on prefix heuristic"
```

### Task 2: Create a Unit Test Script for Markdown Structuring

**Files:**
- Create: `scripts/test_structuring.py`

- [ ] **Step 1: Write a simple mock test script**

Create a simple test using the fake nodes to verify demotion logic works.

```python
import sys
sys.path.insert(0, '.')
from core.models import RawChunk
from core.phase3_structure import structure_nodes_by_markdown

def test_demotion():
    chunks = [
        RawChunk("1", "# Chapter 8 Fragmentation", 1.0),
        RawChunk("2", "This is some intro text.", 2.0),
        RawChunk("3", "# BORROWING CATEGORIES", 3.0),
        RawChunk("4", "This should be a section under chapter 8.", 4.0),
    ]
    tree, _ = structure_nodes_by_markdown(chunks, is_book=True)
    
    assert len(tree) == 1, "Should only have one top-level chapter"
    assert tree[0].text == "Chapter 8 Fragmentation"
    assert len(tree[0].children) == 2, "Should have 2 children (p and h3)"
    assert tree[0].children[1].role == "h3"
    assert tree[0].children[1].text == "BORROWING CATEGORIES"
    print("Test passed: Demotion works correctly!")

if __name__ == "__main__":
    test_demotion()
```

- [ ] **Step 2: Run test and commit**

```bash
python scripts/test_structuring.py
git add scripts/test_structuring.py
git commit -m "test: add mock test for Route C heading demotion logic"
```

### Task 3: Update `SKILL.md`

**Files:**
- Modify: `.agent/skills/v3/SKILL.md`

- [ ] **Step 1: Write the updated documentation**

In `SKILL.md`, under the `phase3_structure.py` logic section or troubleshooting Section 9 (Phase 3), add a note about Route C's demotion heuristic.

```markdown
### Route C (VLM Markdown) の階層補正（Demotion Logic）

VLMはページ単位のローカルな視覚情報しか持たないため、単なる節（サブセクション）の巨大な見出しを誤って `h2` (章) と判定することがあります（Chapter 分断バグ）。
これを防ぐため、`structure_nodes_by_markdown` 関数にて **プレフィックス・ヒューリスティック（Chapter, Part, Preface 等の有無）** を使用しています。
正規の章を示すプレフィックスを持たない `h2` ノードは、強制的に `h3` (節) に降格（Demote）されて直前の章に組み込まれます。
```

- [ ] **Step 2: Commit changes**

```bash
git add .agent/skills/v3/SKILL.md
git commit -m "docs: document Route C heading demotion logic in SKILL.md"
```
