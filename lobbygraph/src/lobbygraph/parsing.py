"""Parsers for the packed and free-text fields of the EU Transparency Register export."""
from __future__ import annotations
import re
import unicodedata

LEGAL_SUFFIXES = r"\b(aisbl|asbl|ivzw|vzw|e\.?\s?v\.?|gmbh|ag|sa|sas|srl|s\.r\.l\.|ltd|limited|bv|b\.v\.|nv|se|plc|inc)\b"


def normalise(name: str) -> str:
    """Lower-case, strip accents, punctuation and legal-form suffixes."""
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ")
    s = re.sub(LEGAL_SUFFIXES, " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_band(value) -> tuple[int | None, int | None]:
    """'3 500 000 - 3 999 999' -> (3500000, 3999999); '<10 000' -> (0, 9999);
    '>= 1 000 000' -> (1000000, None); a plain number -> (n, n)."""
    if value is None:
        return None, None
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return None, None
    m = re.match(r"^([\d\s]+)\s*-\s*([\d\s]+)$", s)
    if m:
        return int(m.group(1).replace(" ", "")), int(m.group(2).replace(" ", ""))
    m = re.match(r"^<\s*([\d\s]+)$", s)
    if m:
        return 0, int(m.group(1).replace(" ", "")) - 1
    m = re.match(r"^>=?\s*([\d\s]+)$", s)
    if m:
        return int(m.group(1).replace(" ", "")), None
    if re.fullmatch(r"\d+(\.\d+)?", s):
        n = int(float(s))
        return n, n
    return None, None


def parse_intermediaries(value) -> list[dict]:
    """'Agora Strategy Group AG (25 000 - 49 999)   Flint Europe (25 000 - 49 999)'
    -> [{'name': 'Agora Strategy Group AG', 'band': '25 000 - 49 999', 'low':..,'high':..}, ...]"""
    if value is None or str(value).strip() in ("", "nan"):
        return []
    out = []
    for m in re.finditer(r"(.+?)\s*\(([^()]*)\)\s*(?=\s{2,}|$)", str(value).strip()):
        name = m.group(1).strip()
        band = m.group(2).strip()
        low, high = parse_band(band)
        out.append({"name": name, "band": band, "low": low, "high": high})
    return out


_INTRO = re.compile(r"(member of|represented in|most (important|relevant)|in alphabetical order|"
                    r"more information|business conduct|approval process|https?://|membership fees)", re.I)


def parse_memberships(value) -> list[str]:
    """Extract association names from the free-text 'Is member of' field."""
    if value is None or str(value).strip() in ("", "nan"):
        return []
    names = []
    for line in re.split(r"[\r\n]+", str(value)):
        line = line.strip().lstrip("•*-–· ").strip()
        if not line:
            continue
        # 'The most important ones at EU level are: A, B, C.'
        m = re.search(r"\bare:\s*(.+)$", line)
        if m:
            names += [p.strip(" .") for p in m.group(1).split(",") if p.strip(" .")]
            continue
        if _INTRO.search(line) or len(line) > 90 or line.endswith(":"):
            continue
        names.append(line.rstrip("."))
    seen, out = set(), []
    for n in names:
        if normalise(n) not in seen:
            seen.add(normalise(n))
            out.append(n)
    return out


def name_variants(name: str) -> list[str]:
    """'VDMA - Mechanical Engineering Industry Association' -> ['VDMA - ...', 'VDMA', 'Mechanical ...'];
    'Business Software Alliance (BSA)' -> [..., 'Business Software Alliance', 'BSA']."""
    v = [name]
    m = re.match(r"^(.*?)\s*\((.*?)\)\s*$", name)
    if m:
        v += [m.group(1), m.group(2)]
    for sep in (" - ", " – "):
        if sep in name:
            v += [p for p in name.split(sep)]
    return [x.strip() for x in v if x and x.strip()]


def split_list(value, seps=r"[;|]") -> list[str]:
    if value is None or str(value).strip() in ("", "nan"):
        return []
    return [p.strip(" .") for p in re.split(seps, str(value)) if p.strip(" .")]


REGISTER_ID = re.compile(r"\b(\d{6,15}-\d{2})\b")


def find_exclusions(text) -> list[str]:
    """Register IDs mentioned as separately listed / excepted in a registration's free text."""
    if text is None:
        return []
    ids = []
    for sentence in re.split(r"(?<=[.\n])\s+", str(text)):
        if re.search(r"exception|separately listed|not included|excluding", sentence, re.I):
            ids += REGISTER_ID.findall(sentence)
    return list(dict.fromkeys(ids))
