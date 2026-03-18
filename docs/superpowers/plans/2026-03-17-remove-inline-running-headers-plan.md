# Remove Inline Running Headers - Evaluation & Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 外部から提供された正規表現や構造のフィードバックを精査し、その中で真に有効な「空行起因のインデックスバグ修正」と「テストの強化」のみを適用しつつ、現在の堅牢な副題保護ロジック（小文字判定）を維持する。

**Architecture:** `core/pdf_ingester.py` の `remove_inline_running_headers` 関数に `non_empty_idx` 変数を導入し、空行 (`not line.strip()`) を `HEADER_CHECK_LIMIT` のカウントから除外します。また、`tests/test_debug_header2.py` にアサーションを追加し、TDD の原則に従って品質を担保します。

**Tech Stack:** Python 3, `pytest`, `re`

---

### Task 1: Fix Empty Line Index Shifting in `pdf_ingester.py`

**Files:**
- Modify: `core/pdf_ingester.py`
- Modify: `tests/test_debug_header.py`

- [ ] **Step 1: Write the failing test (or confirm it fails)**

```python
# tests/test_debug_header.py (.py にて実装済み、アサーションもOKだが、内部の挙動を厳密にテストする)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_debug_header.py -v -s`
Expected: 現在は偶然パスしているか、もしくは潜在的に失敗する。空行が3行以上あるパターンを追加してフェイルさせる。

- [ ] **Step 3: Write minimal implementation**

```python
    HEADER_CHECK_LIMIT = 2
    non_empty_idx = 0
    
    for _, line in enumerate(lines):
        line_processed = line
        if not line.strip():
            processed_lines.append(line_processed)
            continue
            
        if non_empty_idx < HEADER_CHECK_LIMIT:
            for kw in ignored_patterns:
                escaped_kw = re.escape(kw)
                # (\s+|$) で行末も許容
                pattern = re.compile(rf"^({escaped_kw}\s*\d+|\d+\s*{escaped_kw})(\s+|$)", re.IGNORECASE)
                match = pattern.search(line_processed)
                if match:
                    remaining_text = line_processed[match.end():]
                    if not remaining_text:
                        line_processed = "" # 独立行削除
                        break
                    if remaining_text[0].islower():
                        line_processed = remaining_text # 癒着行・本文残し
                        break
        
        non_empty_idx += 1
        processed_lines.append(line_processed)

    # 独立行削除で空になった行を除外して結合
    return '\n'.join(line for line in processed_lines if line.strip() or not line)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_debug_header.py -v -s`
Expected: PASS

### Task 2: Strengthen Assumptions in `test_debug_header2.py`

**Files:**
- Modify: `tests/test_debug_header2.py`

- [ ] **Step 1: Write the failing test / Add assertions**

```python
# tests/test_debug_header2.py
def test_chapter_title_at_top_of_page():
    # ... setup code ...
    res2 = remove_inline_running_headers(text2, keywords)
    assert "The Ethnographic Effect I This is a very long descriptive subtitle" in res2
    assert "17" in res2
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/test_debug_header2.py -v -s`
Expected: PASS (このテストは既存の `islower()` ガードが章タイトルを保護できていることを証明します)

- [ ] **Step 3: Commit**

```bash
git add core/pdf_ingester.py tests/test_debug_header.py tests/test_debug_header2.py docs/superpowers/
git commit -m "fix: ignore empty lines in remove_inline_running_headers max lines check"
```
