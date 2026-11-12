"""Passage identifiers that bind an id to its text.

passage_id = <doc>:<clause>:<sha1-of-text>. The verifier recomputes the hash from the text it
was given, so a citation cannot resolve to text that was altered or invented after retrieval.
"""
from __future__ import annotations

import hashlib
import re


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def content_hash(text: str) -> str:
    return hashlib.sha1(normalise(text).encode("utf-8")).hexdigest()[:10]


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "x"


def make_passage_id(doc_id: str, clause_ref: str, text: str) -> str:
    return f"{slug(doc_id)}:{slug(clause_ref)}:{content_hash(text)}"


def verify_passage_id(passage_id: str, text: str) -> bool:
    return passage_id.rsplit(":", 1)[-1] == content_hash(text)
