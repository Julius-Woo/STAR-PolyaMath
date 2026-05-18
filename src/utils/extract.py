"""Field-extraction helpers for verifier / meta-strategist responses.

These helpers parse the structured sections defined by the
`verifier-review-protocol` and `meta-intervention-protocol` skills so the
orchestrator can record brief, self-contained fields (e.g. failure reason
summaries, forbidden directions) without storing whole transcripts.
"""

from __future__ import annotations

import re
from typing import Iterable


def extract_section_paragraph(text: str, header: str) -> str:
    """Return the first non-empty paragraph under a `## <header>` section.

    A "paragraph" here is the run of consecutive non-empty lines starting at
    the first non-blank line beneath the header, up to the next blank line.
    Returns an empty string if the section is missing or empty.
    """
    if not text or not header:
        return ""
    pat = re.compile(
        r"^\s*##\s+" + re.escape(header) + r"\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    m = pat.search(text)
    if not m:
        return ""
    rest = text[m.end():]
    # Stop at the next "##" (or "#") header.
    end_m = re.search(r"^\s*#{1,2}\s+\S", rest, re.MULTILINE)
    body = rest[: end_m.start()] if end_m else rest

    paragraph: list[str] = []
    started = False
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            if started:
                break
            continue
        # Skip leading bullet/quote markers when collecting the paragraph
        # text (helps when reason was written as a single-line bullet).
        started = True
        paragraph.append(stripped)
    return " ".join(paragraph).strip()


def extract_meta_field(response: str, field: str) -> str:
    """Return the body of a `### FIELD` block, joined as a single string.

    Example:
        ### REASON_SUMMARY
        The plan conflated A with B which is unrecoverable.

    -> "The plan conflated A with B which is unrecoverable."

    Empty string if the field is missing or only contains placeholders like
    `(n/a)` / `(none)`.
    """
    if not response or not field:
        return ""
    pat = re.compile(
        r"^\s*#{2,4}\s+" + re.escape(field) + r"\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    m = pat.search(response)
    if not m:
        return ""
    rest = response[m.end():]
    # Stop at the next "#" header of any depth >= 2.
    end_m = re.search(r"^\s*#{2,4}\s+\S", rest, re.MULTILINE)
    body = rest[: end_m.start()] if end_m else rest

    lines = [ln.rstrip() for ln in body.splitlines()]
    # Drop leading/trailing empties.
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return ""
    joined = "\n".join(lines).strip()
    if joined.lower() in {"(n/a)", "(none)", "n/a", "none"}:
        return ""
    return joined


def extract_meta_bullet_list(response: str, field: str) -> list[str]:
    """Return bullets from a `### FIELD` block as a list of strings.

    Recognises lines starting with `-`, `*`, or `<digit>.` as bullets. Lines
    that are not bullets are ignored. Returns an empty list if the field is
    missing or contains only placeholders.
    """
    body = extract_meta_field(response, field)
    if not body:
        return []
    items: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^(?:[-*]|\d+[.)])\s+(.*)$", line)
        if not m:
            continue
        item = m.group(1).strip()
        if item:
            items.append(item)
    return items


def extract_replan_proposal(reason_text: str) -> str | None:
    """Pick out a `[plan-blocked: <reason>]` tag from a reasoner step report.

    Returns the inner reason string (trimmed) or None if no tag present.
    Multiple tags: returns the last one (most recent claim).
    """
    if not reason_text:
        return None
    matches = re.findall(
        r"\[plan-blocked\s*:\s*([^\]]+)\]",
        reason_text,
        re.IGNORECASE,
    )
    if not matches:
        return None
    return matches[-1].strip()


# ============================================================
# Exploration deliverable parsing
# ============================================================

_EXPLORE_BULLET_SECTIONS = {
    "tried": ("Tried",),
    "worked": ("Worked",),
    "failed": ("Failed",),
    "candidates": ("Candidate Approaches", "Candidates"),
}

_VALID_ASSESSMENTS = {"SOLVED", "PARTIALLY_SOLVED", "NEED_PLAN"}


def _section_body(text: str, headers: tuple[str, ...]) -> str:
    """Return the body of any `## <header>` (case-insensitive) section, joined.

    Stops at the next `##` header. Returns "" if no such section exists.
    """
    for h in headers:
        pat = re.compile(
            r"^\s*##\s+" + re.escape(h) + r"\s*$",
            re.IGNORECASE | re.MULTILINE,
        )
        m = pat.search(text)
        if not m:
            continue
        rest = text[m.end():]
        end_m = re.search(r"^\s*##\s+\S", rest, re.MULTILINE)
        body = rest[: end_m.start()] if end_m else rest
        return body.strip()
    return ""


def _bullets_from_body(body: str) -> list[str]:
    items: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^(?:[-*]|\d+[.)])\s+(.*)$", line)
        if not m:
            continue
        item = m.group(1).strip()
        if item and item.lower() not in {"(none)", "n/a", "none"}:
            items.append(item)
    return items


def parse_exploration_md(text: str) -> dict:
    """Parse a reasoner exploration deliverable into structured fields.

    Returns a dict with keys:
      - tried:       list[str]
      - worked:      list[str]
      - failed:      list[str]
      - candidates:  list[str]
      - assessment:  str  ("SOLVED" | "PARTIALLY_SOLVED" | "NEED_PLAN" | "UNKNOWN")
      - solution_sketch: str   (only if SOLVED; otherwise empty)
      - raw:         str       (original text)
      - parse_ok:    bool      (True if assessment was recognized)

    Robust to extra prose; sections may be missing.
    """
    out = {
        "tried": [],
        "worked": [],
        "failed": [],
        "candidates": [],
        "assessment": "UNKNOWN",
        "solution_sketch": "",
        "raw": text or "",
        "parse_ok": False,
    }
    if not text:
        return out

    for key, headers in _EXPLORE_BULLET_SECTIONS.items():
        body = _section_body(text, headers)
        out[key] = _bullets_from_body(body)

    # Assessment: take the last token-like word in the section that is one of
    # the valid assessments. Tolerate boldface / inline notes.
    body = _section_body(text, ("Assessment",))
    if body:
        for tok in re.findall(r"[A-Z_]+", body):
            up = tok.upper()
            if up in _VALID_ASSESSMENTS:
                out["assessment"] = up
                out["parse_ok"] = True
                break

    # Solution sketch (only meaningful on SOLVED).
    sketch_body = _section_body(text, ("Solution Sketch", "Solution"))
    if sketch_body:
        out["solution_sketch"] = sketch_body

    return out


__all__ = [
    "extract_section_paragraph",
    "extract_meta_field",
    "extract_meta_bullet_list",
    "extract_replan_proposal",
    "parse_exploration_md",
]
