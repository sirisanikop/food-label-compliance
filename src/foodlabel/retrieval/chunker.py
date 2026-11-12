"""Structure-aware chunking for regulation text.

Why not fixed-size windows: in these regulations the answer often sits in a table or schedule
whose heading is several lines above its rows, and a condition may sit in a different chapter
from the rule it modifies. So we:

  1. split on regulation / chapter / schedule headings (a clause is the unit of meaning),
  2. keep a schedule's heading with every chunk cut from that schedule (header repetition),
  3. prefix the text that is EMBEDDED with "doc title | section path | clause" so a query like
     "source of protein" still matches the table row even when the row text is terse,
  4. keep the stored passage text raw (what is cited must be verbatim),
  5. record cross-references ("regulation 7", "Schedule II") as `mentions` so the agent can
     follow them with get_clause.

Pure Python; no ML dependencies. Tune the patterns against your actual PDFs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from foodlabel.retrieval import ids

CHAPTER_RE = re.compile(r"^\s*CHAPTER\s+[IVXLC0-9]+\b.*$", re.I)
SCHEDULE_RE = re.compile(r"^\s*(?:SCHEDULE|ANNEXURE|APPENDIX|TABLE)\b[\s\-–:]*[IVXLC0-9A-Z]*.*$", re.I)
REGULATION_RE = re.compile(r"^\s*(\d{1,3})\.\s+(\S.{2,160})$")
MENTION_RE = re.compile(r"\b(?:regulations?|schedule|table|annexure)\s+[IVXLC0-9]+[A-Za-z]?(?:\(\d+\))?", re.I)


@dataclass
class Block:
    clause_ref: str
    section_path: str
    page: int
    kind: str  # "regulation" | "schedule" | "preamble"
    heading: str
    lines: list[str] = field(default_factory=list)


@dataclass
class Chunk:
    doc_id: str
    doc_role: str
    clause_ref: str
    section_path: str
    page: int
    text: str
    embed_text: str
    passage_id: str
    mentions: list[str]


def parse_blocks(pages: list[str]) -> list[Block]:
    blocks: list[Block] = []
    chapter = ""
    cur = Block("Preamble", "Preamble", 1, "preamble", "")
    for page_no, page in enumerate(pages, start=1):
        for raw in page.splitlines():
            line = raw.rstrip()
            if not line.strip():
                cur.lines.append("")
                continue
            if CHAPTER_RE.match(line):
                chapter = line.strip()
                continue
            if SCHEDULE_RE.match(line) and len(line.strip()) < 140:
                blocks.append(cur)
                heading = line.strip()
                cur = Block(heading, " > ".join(filter(None, [chapter, heading])), page_no, "schedule", heading)
                continue
            m = REGULATION_RE.match(line)
            if m:
                blocks.append(cur)
                ref = f"Reg {m.group(1)}"
                cur = Block(ref, " > ".join(filter(None, [chapter, f"{ref}. {m.group(2).strip()}"])), page_no, "regulation", line.strip())
                cur.lines.append(line.strip())
                continue
            cur.lines.append(line.strip())
    blocks.append(cur)
    return [b for b in blocks if any(l.strip() for l in b.lines)]


def _split(lines: list[str], max_chars: int, overlap_lines: int) -> list[list[str]]:
    """Split on blank-line / row boundaries; never inside a line."""
    parts: list[list[str]] = []
    cur: list[str] = []
    size = 0
    for ln in lines:
        if size + len(ln) + 1 > max_chars and cur:
            parts.append(cur)
            cur = cur[-overlap_lines:] if overlap_lines else []
            size = sum(len(x) + 1 for x in cur)
        cur.append(ln)
        size += len(ln) + 1
    if any(x.strip() for x in cur):
        parts.append(cur)
    return parts


def chunk_blocks(
    blocks: list[Block], *, doc_id: str, doc_role: str, doc_title: str, max_chars: int = 1400, overlap_lines: int = 2
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for b in blocks:
        body = list(b.lines)
        for part in _split(body, max_chars, overlap_lines):
            lines = part
            if b.kind == "schedule" and (not lines or lines[0].strip() != b.heading):
                lines = [b.heading] + lines  # header repetition keeps table rows tied to their heading
            text = "\n".join(lines).strip()
            if not text:
                continue
            mentions = sorted({m.group(0).lower() for m in MENTION_RE.finditer(text)})
            chunks.append(
                Chunk(
                    doc_id=doc_id, doc_role=doc_role, clause_ref=b.clause_ref, section_path=b.section_path,
                    page=b.page, text=text,
                    embed_text=f"{doc_title} | {b.section_path} | {b.clause_ref}\n{text}",
                    passage_id=ids.make_passage_id(doc_id, b.clause_ref, text),
                    mentions=mentions,
                )
            )
    return chunks


def chunk_document(pages: list[str], *, doc_id: str, doc_role: str, doc_title: str, max_chars: int = 1400) -> list[Chunk]:
    return chunk_blocks(parse_blocks(pages), doc_id=doc_id, doc_role=doc_role, doc_title=doc_title, max_chars=max_chars)
