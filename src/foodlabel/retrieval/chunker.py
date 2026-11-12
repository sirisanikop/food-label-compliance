"""Structure-aware chunking for regulation text.

Why not fixed-size windows: in these regulations the answer often sits in a table or schedule
whose heading is several lines above its rows, and a condition may sit in a different chapter
from the rule it modifies. So we:

  1. split on regulation / chapter / schedule headings (a clause is the unit of meaning),
  2. keep a schedule's heading with every chunk cut from that schedule (header repetition),
  3. cut a schedule's table into one chunk per numbered row, so "11. Protein" is its own passage,
  4. prefix the text that is EMBEDDED with "doc title | section path | clause" so a query like
     "source of protein" still matches the table row even when the row text is terse,
  5. keep the stored passage text raw (what is cited must be verbatim),
  6. record cross-references ("regulation 7", "Schedule II") as `mentions` so the agent can
     follow them with get_clause.

Pure Python; no ML dependencies.

Tuned against the FSSAI compendium PDFs (Labelling and Display 2020, Advertising and Claims 2018):
  * a numbered line only starts a regulation if it continues the regulation sequence (it is the
    next number, allowing a small gap) and no schedule has started - otherwise table rows and list
    items such as "11. Protein" were being filed as "Reg 11";
  * a schedule heading is a whole line ("SCHEDULE - IV", "Schedule-I", "3[SCHEDULE - II" with a
    footnote marker) that is upper-case or is followed by a "(See regulation ...)" line, so a
    wrapped sentence that merely starts with "Schedule II of these regulations" is not a heading;
  * the compendium's running page header ("Version-IV (14.12.2022)") is dropped;
  * "11.Approval of claims" (no space after the dot) is recognised.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from foodlabel.retrieval import ids

CHAPTER_RE = re.compile(r"^\s*CHAPTER\b[\s\-–—:.]*[IVXLC0-9]+\b.*$")
SCHEDULE_HEAD_RE = re.compile(r"^(SCHEDULE|ANNEXURE|APPENDIX)\s*[-–—:]?\s*([IVXLC]+[A-Z]?|\d+[A-Z]?|[A-Z])\s*$", re.I)
FOOTNOTE_PREFIX_RE = re.compile(r"^\d{1,2}\[\s*")
SEE_RE = re.compile(r"^[(\[]\s*(?:see|refer)\b", re.I)
REGULATION_RE = re.compile(r"^\s*(\d{1,3})\.\s*([A-Z\[“\"].{2,200})$")
ROW_RE = re.compile(r"^(\d{1,2})\.(?:\s+(.*))?$")
RUNNING_HEADER_RE = re.compile(r"^\s*Version\s*[-–—]\s*[IVXLC]+\s*\(\d{1,2}\.\d{1,2}\.\d{4}\)\s*$", re.I)
MENTION_RE = re.compile(r"\b(?:regulations?|schedule|table|annexure)\s+[IVXLC0-9]+[A-Za-z]?(?:\(\d+\))?", re.I)
_TITLE_CUT_RE = re.compile(r"(?:[.:]\s*[-–—]|(?<=\s)[-–—](?=[\s(]|$)|[-–—](?=\s*\())")
_ROW_TITLE_CUT_RE = re.compile(r"\s{2,}|\s+(?:Low|Free|Source|High|Rich|Very low|Reduced|Increased|Not more|At least|Shall)\b")
REGULATION_GAP = 3  # tolerate a few omitted/unparsed regulation numbers when checking the sequence


@dataclass
class Block:
    clause_ref: str
    section_path: str
    page: int
    kind: str  # "regulation" | "schedule" | "preamble"
    heading: str
    lines: list[str] = field(default_factory=list)
    line_pages: list[int] = field(default_factory=list)

    def add(self, line: str, page: int) -> None:
        self.lines.append(line)
        self.line_pages.append(page)


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


def _flatten(pages: list[str]) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for page_no, page in enumerate(pages, start=1):
        for raw in page.splitlines():
            if RUNNING_HEADER_RE.match(raw):
                continue
            out.append((page_no, raw.rstrip()))
    return out


def _next_nonblank(entries: list[tuple[int, str]], i: int):
    for j in range(i + 1, len(entries)):
        if entries[j][1].strip():
            return j
    return None


def _schedule_label(line: str, next_line: str) -> str | None:
    """'Schedule I' / 'Schedule IIA' if `line` is a schedule heading, else None."""
    s = FOOTNOTE_PREFIX_RE.sub("", line.strip()).rstrip("] ").strip()
    m = SCHEDULE_HEAD_RE.match(s)
    if not m:
        return None
    keyword, designator = m.group(1), m.group(2).upper()
    if keyword.isupper() or SEE_RE.match(next_line.strip()):
        return f"{keyword.capitalize()} {designator}"
    return None


def _reg_title(rest: str) -> str:
    title = _TITLE_CUT_RE.split(rest, maxsplit=1)[0].strip(" .:-–—")
    return (title or rest.strip())[:90]


def _row_title(rest: str) -> str:
    return _ROW_TITLE_CUT_RE.split(rest.strip(), maxsplit=1)[0].strip(" .:")[:40]


def _split_rows(block: Block) -> list[Block]:
    """Cut a schedule into one block per numbered table row (row numbers must increase)."""
    pre = Block(block.clause_ref, block.section_path, block.page, "schedule", block.heading)
    out: list[Block] = []
    cur, prev = pre, 0
    for line, page in zip(block.lines, block.line_pages):
        s = line.strip()
        m = ROW_RE.match(s) if s else None
        if m and int(m.group(1)) > prev:
            prev = int(m.group(1))
            out.append(cur)
            title = _row_title(m.group(2) or "")
            path = f"{block.section_path} > Row {prev}" + (f": {title}" if title else "")
            cur = Block(block.clause_ref, path, page, "schedule", block.heading)
            cur.add(s, page)
            continue
        cur.add(line, page)
    out.append(cur)
    return out


def parse_blocks(pages: list[str]) -> list[Block]:
    entries = _flatten(pages)
    blocks: list[Block] = []
    chapter = ""
    last_reg = 0
    in_schedule = False
    skip: set[int] = set()
    cur = Block("Preamble", "Preamble", 1, "preamble", "")
    for i, (page_no, raw) in enumerate(entries):
        if i in skip:
            continue
        line = raw.strip()
        if not line:
            cur.add("", page_no)
            continue
        if CHAPTER_RE.match(line) and len(line) < 60:
            chapter = line
            j = _next_nonblank(entries, i)
            if j is not None:
                title = entries[j][1].strip()
                if title.isupper() and len(title) <= 80 and not CHAPTER_RE.match(title):
                    chapter = f"{chapter} {title}"
                    skip.add(j)
            continue
        nb = _next_nonblank(entries, i)
        label = _schedule_label(line, entries[nb][1] if nb is not None else "") if len(line) < 60 else None
        if label:
            blocks.append(cur)
            subs: list[str] = []
            j = i
            for _ in range(2):  # keep "(See regulation ...)" and the schedule title with the heading
                j = _next_nonblank(entries, j)
                if j is None:
                    break
                t = entries[j][1].strip()
                is_see = bool(SEE_RE.match(t))
                is_title = not re.search(r"\d", t) and not t.endswith((".", ";", ":", ","))
                if len(t) > 90 or ROW_RE.match(t) or not (is_see or is_title):
                    break
                subs.append(t)
                skip.add(j)
            heading = " ".join([label] + subs)
            in_schedule = True
            cur = Block(label, " > ".join(filter(None, [chapter, heading])), page_no, "schedule", heading)
            continue
        m = REGULATION_RE.match(line)
        if m and not in_schedule:
            n = int(m.group(1))
            if last_reg == 0 or last_reg < n <= last_reg + REGULATION_GAP:
                last_reg = n
                blocks.append(cur)
                ref = f"Reg {n}"
                cur = Block(ref, " > ".join(filter(None, [chapter, f"{ref}. {_reg_title(m.group(2))}"])), page_no, "regulation", line)
                cur.add(line, page_no)
                continue
        cur.add(line, page_no)
    blocks.append(cur)
    out: list[Block] = []
    for b in blocks:
        out.extend(_split_rows(b) if b.kind == "schedule" else [b])
    return [b for b in out if any(l.strip() for l in b.lines)]


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
            embed_text = f"{doc_title} | {b.section_path} | {b.clause_ref}\n{text}"
            if b.kind == "schedule" and "> Row " in b.section_path:
                # Table rows are embedded as "<row no>: <title>" + the row's own text. Repeating the long schedule
                # heading (in the prefix and again in the text) made every row look alike to the embedding model
                # and "Source of protein" ranked the dietary-fibre row first; measured with eval/retrieval_probe.py.
                row_title = b.section_path.split("> Row ", 1)[1]
                embed_text = f"{row_title}\n" + "\n".join(lines[1:])  # lines[0] is the repeated heading
            chunks.append(
                Chunk(
                    doc_id=doc_id, doc_role=doc_role, clause_ref=b.clause_ref, section_path=b.section_path,
                    page=b.page, text=text,
                    embed_text=embed_text,
                    passage_id=ids.make_passage_id(doc_id, b.clause_ref, text),
                    mentions=mentions,
                )
            )
    return chunks


def chunk_document(pages: list[str], *, doc_id: str, doc_role: str, doc_title: str, max_chars: int = 1400) -> list[Chunk]:
    return chunk_blocks(parse_blocks(pages), doc_id=doc_id, doc_role=doc_role, doc_title=doc_title, max_chars=max_chars)
