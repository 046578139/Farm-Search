"""Which rows of a parcel layer are real assessment accounts.

The Maryland parcel layer carries placeholder IDs for polygons that are not
accounts: 'ROW' (road right-of-way), 'WATER', 'WATER_CANAL', 'WATER_IS',
'RAILROAD', 'RR', 'UNK', 'UNKNOWN', 'NO ID', 'GCE' / 'LCE' (condominium
common elements), 'COMMON', 'OS' (open space), 'SWM' (stormwater), 'PARK',
'PRIVATE ROW', 'ROW_ALLEY', and nulls. Dissolved by account ID these become
2,000-acre "parcels" that pass every filter and block frontage as
"neighbours", so they must be recognised structurally, not by list alone:
a real SDAT account ID is at least 8 characters and always contains digits.
"""
from __future__ import annotations

from typing import Iterable, Optional

import pandas as pd

# Real Maryland SDAT account IDs: 8+ alphanumerics with at least one digit
# (e.g. 1101000098, 1102502WH). Placeholders are short, all-letters, or
# contain spaces / underscores.
MD_ACCOUNT_ID_REGEX = r"^(?=.*\d)[0-9A-Za-z]{8,}$"
_NULL_STRINGS = {"", "NONE", "NAN", "NULL", "<NULL>"}


def non_account_mask(acct: pd.Series, listed_ids: Iterable[str] = (), regex: Optional[str] = MD_ACCOUNT_ID_REGEX) -> pd.Series:
    """True where the value is not a real account ID: null, a listed
    placeholder, or (when `regex` is given) not matching the account-ID
    pattern. Pass regex=None to rely on the list alone (synthetic data)."""
    s = acct.astype("string").fillna("").str.strip()
    upper = s.str.upper()
    listed = {str(x).strip().upper() for x in listed_ids}
    mask = acct.isna() | upper.isin(_NULL_STRINGS) | upper.isin(listed)
    if regex:
        mask = mask | ~s.str.fullmatch(regex)
    return mask.fillna(True).astype(bool)


def owner_type_from_exemption(desc: Optional[str]) -> Optional[str]:
    """Owner type implied by SDAT's exemption-class description (DESCEXCL),
    e.g. 'STA Parks', 'JUR Schools (Public...)', 'NPF Other', 'PVT Churches,
    Synagogues, & Parsonages'. The prefix names the holder class:
    STA state, PUB public/federal, MUN municipal, JUR county -> government;
    NPF nonprofit, PVT church* -> religious_nonprofit. Anything else -> None."""
    if desc is None:
        return None
    s = str(desc).strip().upper()
    if not s or s == "NAN":
        return None
    prefix = s.split(" ", 1)[0]
    if prefix in {"STA", "PUB", "MUN", "JUR", "FED"}:
        return "government"
    if prefix == "NPF":
        return "religious_nonprofit"
    if prefix == "PVT" and ("CHURCH" in s or "RELIG" in s or "SYNAG" in s or "PARSON" in s or "CEMET" in s):
        return "religious_nonprofit"
    return None
