# BugHound Mini Model Card (Reflection)

Fill this out after you run BugHound in **both** modes (Heuristic and Gemini).

---

## 1) What is this system?

**Name:** BugHound  
**Purpose:** Analyze a Python snippet, propose a fix, and run reliability checks before suggesting whether the fix should be auto-applied.

**Intended users:** Students learning agentic workflows and AI reliability concepts.

---

## 2) How does it work?

BugHound follows a five-step agent loop:

1. **Plan** — The agent logs that it is about to scan the snippet and propose a fix. There is no separate planning LLM call; this step is mostly for transparency in the UI logs.

2. **Analyze** — BugHound looks for issues in the input code.
   - **Heuristic mode** uses simple string/regex checks: `print(`, bare `except:`, and `TODO` comments.
   - **Gemini mode** sends the code to the LLM with a system prompt that asks for a JSON array of issues (`type`, `severity`, `msg`). If the API fails or the response is not valid JSON, it falls back to heuristics.

3. **Act** — If issues are found, BugHound proposes a fixed version of the code.
   - **Heuristics** replace bare `except:` with `except Exception as e:` and swap `print(` for `logging.info(` (adding `import logging` if needed).
   - **Gemini** rewrites the full snippet with instructions to make minimal, behavior-preserving changes.

4. **Test** — The `assess_risk` function scores the proposed fix (0–100) based on issue severity and structural changes (e.g., new imports, removed `return` statements, big line-count drops).

5. **Reflect** — If the risk level is `"low"` (score ≥ 75), `should_autofix` is `True`. Otherwise BugHound recommends human review before applying the change.

---

## 3) Inputs and outputs

**Inputs:**

- I tested the four sample snippets in `sample_code/`: `print_spam.py`, `flaky_try_except.py`, `mixed_issues.py`, and `cleanish.py`.
- The inputs were short scripts (roughly 5–10 lines): single functions, `try/except` blocks, and a mix of code-quality and reliability problems. Nothing with classes, async, or multiple files.

**Outputs:**

- **Issues detected:** Code quality (`print` statements), reliability (bare `except:`), and maintainability (`TODO` comments). In Gemini mode I also saw resource management (file not opened with `with`) and more specific error-handling categories.
- **Fixes proposed:** Heuristic fixes were very mechanical — add `import logging`, replace `print(`, patch bare `except:`. Gemini fixes were more contextual, like using `with open(...)` in `flaky_try_except.py`, but sometimes incomplete (e.g., `print_spam.py` still had `print` calls after the fix).
- **Risk report:** Most fixes landed at **medium** risk with `should_autofix: False`. The main reasons were new imports (−25) and high-severity issues (−40). `cleanish.py` scored 100 / low and was the only case flagged as safe to auto-apply, since it had no issues and the code was unchanged.

---

## 4) Reliability and safety rules

### Rule 1: New import penalty

- **What it checks:** Whether the fixed code introduces import lines that were not in the original (`_import_lines` diff).
- **Why it matters:** Adding dependencies (like `logging`) changes how the module behaves at import time and can break environments that do not expect extra imports. A "small" fix that silently adds imports is worth a human glance.
- **False positive:** A fix that genuinely needs a new import (e.g., switching from `print` to `logging`) gets penalized even when the change is correct and low-risk.
- **False negative:** A fix that rewrites logic without adding imports but still changes behavior (e.g., silently changing a return value) would not be caught by this rule.

### Rule 2: Return statement removal check

- **What it checks:** If the original code contains `return` but the fixed code does not, the score drops by 30 points.
- **Why it matters:** Accidentally dropping a `return` can turn a working function into one that returns `None`, which is a subtle but serious bug — especially easy for an LLM to do when rewriting a function.
- **False positive:** A refactor that intentionally moves `return` into a helper function (still valid Python) might trigger this if the top-level function no longer has a `return` keyword.
- **False negative:** A fix that keeps `return` but changes *what* is returned (e.g., `return 0` instead of re-raising) would not be flagged.

---

## 5) Observed failure modes

### Example 1: Missed issue (heuristic mode)

**Snippet:** `flaky_try_except.py`

```python
def load_text_file(path):
    try:
        f = open(path, "r")
        data = f.read()
        f.close()
    except:
        return None
    return data
```

**What went wrong:** Heuristics only flagged the bare `except:` and replaced it with `except Exception as e:`. They did **not** notice that the file is opened without a `with` statement, so if `f.read()` throws, the file handle may never be closed. Gemini caught this as a "Resource Management" issue and rewrote it using `with open(...)`.

### Example 2: Risky or incomplete fix (Gemini mode)

**Snippet:** `print_spam.py`

**What went wrong:** Gemini flagged the `print` statements and added `import logging` plus a `logger`, but the fixed code still contained two bare `print("Hello", name)` and `print("Welcome!")` calls. So the fix looked more sophisticated but did not actually resolve all the issues it reported. The risk scorer still penalized the new import, which felt right — but a user might assume the prints were fully handled because a fix was proposed.

---

## 6) Heuristic vs Gemini comparison

- **What Gemini detected that heuristics did not:** File handle leaks (`with` statement missing) in `flaky_try_except.py`. Gemini also gave more nuanced severity labels (e.g., splitting error handling vs. resource management) instead of lumping everything into three pattern matches.
- **What heuristics caught consistently:** `print(`, bare `except:`, and `TODO` — every time, with no API latency or parsing failures.
- **How fixes differed:** Heuristics made predictable, regex-based edits (always `logging.info`, always `except Exception as e:`). Gemini produced cleaner rewrites when it worked (context manager for files) but was inconsistent on `print_spam.py`. Heuristics actually replaced *all* prints; Gemini left some behind.
- **Risk scorer vs. intuition:** Mostly aligned. I expected `mixed_issues.py` to be high-risk because it had a high-severity bare `except` plus multiple issue types, and it scored 5 (high) in heuristic mode. Gemini's simpler fix for `mixed_issues.py` scored medium (45), which also felt reasonable since the rewrite was small. I was glad that `print_spam.py` was **not** auto-fixed in either mode because of the new-import penalty — even though the severity was only "Low."

---

## 7) Human-in-the-loop decision

**Scenario:** BugHound should refuse to auto-fix when the proposed fix removes a large chunk of the original code (e.g., more than 50% of lines).

**Why:** In my Gemini run on `mixed_issues.py`, the fix dropped the `print`, the `TODO` comment, and the logging-related context, leaving only a bare `try/except`. That kind of aggressive trimming can hide intentional placeholders or remove comments the author meant to keep.

**Trigger:** If `len(fixed_lines) < len(original_lines) * 0.5`, set `should_autofix = False` regardless of score. (The score already deducts 20 points for this, but a medium score could still slip through in edge cases.)

**Where to implement:** In `risk_assessor.py`, as a hard override after the score is computed — similar to how empty fixes return `should_autofix: False` immediately.

**User message:** *"This fix removes a large portion of your original code. Please review the diff carefully before applying — automated changes that delete significant code are not applied automatically."*

---

## 8) Improvement idea

Add a post-fix validation step that checks whether each reported issue was actually addressed. For example, if the analyzer flagged `print` statements but the fixed code still contains `print(`, downgrade confidence and force human review. This would have caught the incomplete Gemini fix on `print_spam.py` without needing a much more complex system — just a few string/regex checks mirroring the heuristic rules, run *after* the fixer step. It keeps the architecture the same (analyze → act → test → reflect) but makes the "test" phase smarter about fix completeness, not just structural risk.
