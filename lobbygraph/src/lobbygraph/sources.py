"""Readers for the raw sources. Each returns plain records; no graph logic here."""
from __future__ import annotations
import json
import time
from pathlib import Path

import pandas as pd
import requests

REGISTER_SOURCE = "EU Transparency Register bulk export (data.europa.eu)"
REGISTER_URL = "https://data.europa.eu/data/datasets/transparency-register"
GLEIF_API = "https://api.gleif.org/api/v1"

COL = {  # short names for the export's long column headers
    "id": "Identification Number",
    "name": "Name",
    "acronym": "Acronym",
    "category": "Category of registration",
    "registered": "Registration date",
    "hq_city": "Head office city",
    "hq_country": "Head office country",
    "cost": "Annual cost for register activity or total budget",
    "fte": "Members FTE",
    "people": "Members",
    "fy_start": "Closed year start",
    "fy_end": "Closed year end",
    "intermediaries": "Closed year: Intermediary (cost) or client (revenue): EU legislative proposal",
    "member_of": "Is member of: List of associations, (con)federations, networks and other bodies of which the organisation is a member",
    "fields": "Field of interest",
    "files": "EU Legislative proposals/policies",
    "staff_text": "Members Complementary information",
    "extra_text": "Complementary Information",
    "eu_grants": "Closed year total EU grants",
}


def read_register(path: str | Path, cache_dir: str | Path | None = None) -> pd.DataFrame:
    """Read the register export (.xlsx, .csv or a cached .pkl). The original download is .xls;
    convert it once with LibreOffice: soffice --headless --convert-to xlsx <file>."""
    path = Path(path)
    if path.suffix == ".pkl":
        return pd.read_pickle(path)
    if cache_dir:
        cache = Path(cache_dir) / (path.stem + ".pkl")
        if cache.exists() and cache.stat().st_mtime > path.stat().st_mtime:
            return pd.read_pickle(cache)
    df = (pd.read_csv(path, dtype=str) if path.suffix == ".csv"
          else pd.read_excel(path, dtype=str))
    if cache_dir:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        df.to_pickle(Path(cache_dir) / (path.stem + ".pkl"))
    return df


class Gleif:
    """GLEIF client. Online: calls api.gleif.org (fetches every ultimate child of a group parent).
    Offline: answers from a JSON cache of normalised entity records."""

    def __init__(self, cache_file: str | Path, online: bool = False, pause: float = 0.3):
        self.cache_file = Path(cache_file)
        self.online = online
        self.pause = pause
        data = json.loads(self.cache_file.read_text()) if self.cache_file.exists() else {"entities": []}
        self.meta = {k: v for k, v in data.items() if k != "entities"}
        self.entities = {e["lei"]: e for e in data["entities"]}

    # ---- online helpers -------------------------------------------------
    def _get(self, url, params=None):
        r = requests.get(url, params=params, timeout=30, headers={"Accept": "application/vnd.api+json"})
        time.sleep(self.pause)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _normalise(rec, retrieved):
        a = rec["attributes"]
        ent = a["entity"]
        return {"lei": a["lei"], "legal_name": ent["legalName"]["name"],
                "country": ent["legalAddress"]["country"], "city": ent["legalAddress"]["city"],
                "hq_city": (ent.get("headquartersAddress") or {}).get("city"),
                "registered_as": ent.get("registeredAs"), "status": ent.get("status"),
                "lei_status": (a.get("registration") or {}).get("status"),
                "next_renewal": (a.get("registration") or {}).get("nextRenewalDate"),
                "ultimate_parent_lei": None, "ultimate_parent_exception": None,
                "retrieved": retrieved, "note": None}

    def fetch_group(self, parent_lei: str, retrieved: str):
        """Fetch the parent and all its ultimate children (paged) into the cache."""
        rec = self._get(f"{GLEIF_API}/lei-records/{parent_lei}")
        self.entities[parent_lei] = self._normalise(rec["data"], retrieved)
        exc = self._get(f"{GLEIF_API}/lei-records/{parent_lei}/ultimate-parent-reporting-exception")
        if exc:
            self.entities[parent_lei]["ultimate_parent_exception"] = exc["data"]["attributes"]["reason"]
        page = 1
        while True:
            res = self._get(f"{GLEIF_API}/lei-records/{parent_lei}/ultimate-children",
                            {"page[size]": 200, "page[number]": page})
            if not res:
                break
            for child in res["data"]:
                e = self._normalise(child, retrieved)
                e["ultimate_parent_lei"] = parent_lei
                self.entities[e["lei"]] = e
            self.meta.setdefault("ultimate_children_total", {})[parent_lei] = res["meta"]["pagination"]["total"]
            if page >= res["meta"]["pagination"]["lastPage"]:
                break
            page += 1

    def fetch_entity(self, lei: str, retrieved: str):
        """Fetch one entity outside the configured groups (e.g. a stake target) with its ultimate parent."""
        if lei in self.entities:
            return
        rec = self._get(f"{GLEIF_API}/lei-records/{lei}")
        if not rec:
            return
        e = self._normalise(rec["data"], retrieved)
        par = self._get(f"{GLEIF_API}/lei-records/{lei}/ultimate-parent")
        if par and par.get("data"):
            e["ultimate_parent_lei"] = par["data"]["attributes"]["lei"]
        else:
            exc = self._get(f"{GLEIF_API}/lei-records/{lei}/ultimate-parent-reporting-exception")
            if exc:
                e["ultimate_parent_exception"] = exc["data"]["attributes"]["reason"]
        self.entities[lei] = e

    def save(self):
        self.cache_file.write_text(json.dumps({**self.meta, "entities": list(self.entities.values())},
                                              indent=1, ensure_ascii=False))

    # ---- lookups ------------------------------------------------------------
    def get(self, lei: str) -> dict | None:
        return self.entities.get(lei)

    def children_of(self, parent_lei: str) -> list[dict]:
        return [e for e in self.entities.values() if e.get("ultimate_parent_lei") == parent_lei]


def read_meetings(meetings_csv: str | Path, officials_csv: str | Path | None = None):
    """Commission meetings, one row per (meeting, registration). The PoC uses a sample extract;
    the full Commission meetings file from data.europa.eu is mapped to the same columns."""
    m = pd.read_csv(meetings_csv, dtype=str)
    o = pd.read_csv(officials_csv, dtype=str) if officials_csv else None
    return m, o


MEETINGS_URL = "https://data.europa.eu/data/datasets/european-commission-meetings-with-interest-representatives"


def _split(v):
    return [p.strip() for p in str(v).split(",") if p.strip() and str(v) != "nan"]


def read_commission_meetings(cabinet_xlsx=None, dg_xlsx=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read the Commission's published meeting lists into one table per meeting and one per official.

    Cabinet file: one row per meeting; officials and their titles are comma-separated.
    Directors-General file: one row per official per meeting; rows are grouped back into meetings.
    Register IDs of several attending organisations share one comma-separated cell.
    Returns (meetings, attendance) where attendance has one row per (meeting, official)."""
    import hashlib
    frames = []
    if cabinet_xlsx:
        c = pd.read_excel(cabinet_xlsx, header=1, dtype=str)
        frames.append(pd.DataFrame({
            "level": "cabinet", "unit": c["Name of cabinet"], "unit_acronym": None,
            "officials": c["Name of EC representative"], "titles": c["Title of EC representative"],
            "date": c["Date of meeting"], "location": c["Location"], "organisations": c["Name of interest representative"],
            "register_ids": c["Transparency register ID"], "subject": c["Subject of the meeting"],
            "source": "European Commission: meetings of Commission representatives (2024-2029)"}))
    if dg_xlsx:
        d = pd.read_excel(dg_xlsx, header=1, dtype=str)
        frames.append(pd.DataFrame({
            "level": "directorate-general", "unit": d["Name of DG/EA - full name"],
            "unit_acronym": d["Name of DG/EA - acronym"],
            "officials": d["Name of EC/EA representative"], "titles": d["Title of EC/EA representative"],
            "date": d["Date of meeting"], "location": d["Location"], "organisations": d["Name of interest representative"],
            "register_ids": d["Transparency register ID"], "subject": d["Subject of the meeting"],
            "source": "European Commission: meetings of Directors-General and management staff"}))
    raw = pd.concat(frames, ignore_index=True).fillna({"subject": "", "location": "", "register_ids": ""})
    raw["subject"] = raw["subject"].str.replace(r"\s+", " ", regex=True).str.strip()
    raw["ids"] = raw["register_ids"].map(lambda v: sorted(set(_split(v))))
    key = raw["level"] + "|" + raw["unit"].fillna("") + "|" + raw["date"] + "|" + raw["location"] + "|" + \
        raw["ids"].map(",".join) + "|" + raw["subject"]
    raw["meeting_id"] = key.map(lambda k: "MTG-" + hashlib.sha1(k.encode()).hexdigest()[:12])
    meetings = (raw.groupby("meeting_id", as_index=False)
                .agg(level=("level", "first"), unit=("unit", "first"), unit_acronym=("unit_acronym", "first"),
                     date=("date", "first"), location=("location", "first"), subject=("subject", "first"),
                     organisations=("organisations", "first"), ids=("ids", "first"), source=("source", "first"),
                     source_rows=("level", "size")))
    meetings["source_url"] = MEETINGS_URL
    att = []
    for mid, names, titles, unit in zip(raw["meeting_id"], raw["officials"], raw["titles"], raw["unit"]):
        ns, ts = _split(names), _split(titles)
        for i, n in enumerate(ns):
            att.append({"meeting_id": mid, "official": n, "title": ts[i] if i < len(ts) else None, "unit": unit})
    attendance = pd.DataFrame(att).drop_duplicates()
    return meetings, attendance
