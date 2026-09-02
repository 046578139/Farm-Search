"""Owner-name handling: entity typing, normalization, same-owner tests.

Used by Stage 1 (owner_type) and Stage 4 (reserve-strip owner comparison), and
later by the deduplicated owner list. Names in SDAT are free text in inconsistent
formats ("SMITH JOHN A & MARY B", "SMITH JOHN A ET AL", "J & M FARMS LLC"), so
matching is token-based and deliberately forgiving.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

# Ordered: the first matching bucket wins. Government/religious checked before
# corporate because "BOARD OF EDUCATION INC" style names exist.
_PATTERNS = [
    ("government", re.compile(
        r"\b(STATE OF MARYLAND|MARYLAND DEPT|DEPARTMENT OF|COUNTY COMMISSIONERS|"
        r"BOARD OF (COUNTY )?COMMISSIONERS|BOARD OF EDUCATION|COUNTY OF|CITY OF|TOWN OF|"
        r"MUNICIPAL|UNITED STATES|U ?S ?A\b|DEPT OF NATURAL RESOURCES|DNR\b|"
        r"MARYLAND ENVIRONMENTAL TRUST|SANITARY COMM|HOUSING AUTHORITY)\b")),
    ("religious_nonprofit", re.compile(
        r"\b(CHURCH|PARISH|CONGREGATION|DIOCESE|ARCHDIOCESE|MINISTRIES|MINISTRY|"
        r"CEMETERY|SYNAGOGUE|TEMPLE|MOSQUE|FOUNDATION|CONSERVANCY|LAND TRUST|"
        r"BRETHREN|MENNONITE|LUTHERAN|METHODIST|BAPTIST|CATHOLIC|PRESBYTERIAN|"
        r"FRIENDS MEETING|SOCIETY OF)\b")),
    ("trust", re.compile(
        r"\b(TRUST(EE|EES)?|TRS?|REVOCABLE|IRREVOCABLE|LIVING TR|FAMILY TR|"
        r"REV TR|TR AGREEMENT|U/A|UAD|ESTATE OF|EST OF|LIFE ESTATE)\b")),
    ("llc", re.compile(r"\b(L\.?L\.?C\.?|LLLP|L\.?L\.?P\.?|PLLC)\b")),
    ("corporation", re.compile(
        r"\b(INC|INCORPORATED|CORP|CORPORATION|CO\b|COMPANY|LTD|LIMITED|L\.?P\.?|"
        r"PARTNERSHIP|PARTNERS|ASSOCIATES|ASSOC|ASSN|ASSOCIATION|ENTERPRISES|"
        r"HOLDINGS|PROPERTIES|INVESTMENTS|DEVELOPMENT|DEVELOPERS|GROUP|VENTURES|"
        r"FARMS? INC|REALTY)\b")),
]

_SUFFIX_NOISE = re.compile(
    r"\b(ET ?AL|ETALS?|ET ?UX|ET ?VIR|AND WIFE|& WIFE|AND HUSBAND|& HUSBAND|"
    r"H/W|W/H|T/E|TE\b|JT|JTWROS|TRUSTEES?|TRS?|CO-?TRUSTEES?|LIFE ESTATE|"
    r"JR|SR|II|III|IV|MRS?|MS|DR)\b")


def classify_owner(name: Optional[str]) -> str:
    """Bucket an owner name into individual / llc / corporation / trust /
    government / religious_nonprofit / unknown."""
    if name is None:
        return "unknown"
    s = str(name).strip().upper()
    if not s:
        return "unknown"
    s = s.replace(".", "")
    s = re.sub(r",", " ", s)
    s = re.sub(r"\s+", " ", s)
    for label, pat in _PATTERNS:
        if pat.search(s):
            return label
    return "individual"


def normalize_owner_name(name: Optional[str]) -> str:
    """Uppercase, strip punctuation and relationship/suffix noise, collapse spaces."""
    if name is None:
        return ""
    s = str(name).upper()
    s = s.replace("&", " AND ")
    s = re.sub(r"[^\w\s/]", " ", s)
    s = _SUFFIX_NOISE.sub(" ", s)
    s = re.sub(r"\bAND\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def owner_tokens(name: Optional[str]) -> set[str]:
    return {t for t in normalize_owner_name(name).split(" ") if len(t) > 1}


def normalize_address(*parts: Optional[str]) -> str:
    s = " ".join(str(p) for p in parts if p is not None and str(p).strip() and str(p).lower() != "nan")
    s = s.upper()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\b(ROAD)\b", "RD", s)
    s = re.sub(r"\b(STREET)\b", "ST", s)
    s = re.sub(r"\b(DRIVE)\b", "DR", s)
    s = re.sub(r"\b(LANE)\b", "LN", s)
    s = re.sub(r"\b(AVENUE)\b", "AVE", s)
    s = re.sub(r"\b(COURT)\b", "CT", s)
    s = re.sub(r"\b(PIKE)\b", "PK", s)
    s = re.sub(r"\b(P ?O BOX)\b", "POBOX", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Zip+4 -> zip5
    s = re.sub(r"\b(\d{5})\s?\d{4}\b", r"\1", s)
    return s


def owners_match(name_a: Optional[str], name_b: Optional[str],
                 addr_a: Optional[str] = None, addr_b: Optional[str] = None,
                 min_jaccard: float = 0.6) -> bool:
    """True when two SDAT owner records plausibly describe the same owner.

    Name match: Jaccard similarity of normalized tokens >= min_jaccard, OR one
    token set contains the other (handles "SMITH JOHN" vs "SMITH JOHN A & MARY").
    Address match: identical normalized mailing address (strong signal — one
    farmer's LLC and personal holdings share a mailbox).
    """
    ta, tb = owner_tokens(name_a), owner_tokens(name_b)
    if ta and tb:
        inter = len(ta & tb)
        union = len(ta | tb)
        if union and inter / union >= min_jaccard:
            return True
        if inter and (ta <= tb or tb <= ta):
            return True
    if addr_a and addr_b:
        na, nb = normalize_address(addr_a), normalize_address(addr_b)
        if na and nb and na == nb:
            return True
    return False


def owner_key(name: Optional[str], address: Optional[str]) -> str:
    """Stable key for collapsing parcels by owner + mailing address."""
    return f"{normalize_owner_name(name)}|{normalize_address(address)}"


def join_address(row, fields: Iterable[str]) -> str:
    vals = []
    for f in fields:
        if f in row and row[f] is not None:
            v = str(row[f]).strip()
            if v and v.lower() != "nan":
                vals.append(v)
    return " ".join(vals)
