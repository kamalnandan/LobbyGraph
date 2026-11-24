"""Theme tagging: links meetings and lobby registrations to a short list of energy themes.

Simple, explainable keyword rules (English, French, Spanish, Italian, German), no AI.
Every tag keeps the matched words and the text it came from, so each link can be checked.
Rules are matched on lower-case text; \\b marks a word boundary.
"""
from __future__ import annotations
import re

# id, display name, [(regex, confidence)], [exclusion regex removed from the text before matching]
THEMES = [
    ("gas", "Gas and LNG", [
        (r"\blng\b|liquefied natural gas|natural gas|\bgas (?:market|package|supply|supplies|infrastructure|storage|demand|price|prices|security|sector|import|imports|transit|network|networks|pipeline)|gas[ &,/]+(?:and )?hydrogen|hydrogen[ &,/]+(?:and )?gas|\bgas and power\b|\bgas, power\b|methane (?:regulation|emission|emissions)|\bbiomethane\b|\bbiogas\b|\bgaz\b|\bgas naturale\b|\bgas natural\b|\berdgas\b|\bpipeline|\bremit\b", "high"),
        (r"\bgas\b", "medium"),
    ], [r"greenhouse gas(?:es)?", r"\bf-?gas(?:es)?\b", r"gaz fluorés", r"gaz à effet de serre", r"gases? de efecto invernadero"]),
    ("hydrogen", "Hydrogen", [
        (r"hydrogen|hydrogène|hidrógeno|idrogeno|wasserstoff|electrolys|elektrolys|\bammonia\b", "high"),
        (r"\bh2\b", "medium"),
    ], []),
    ("renewables", "Renewables", [
        (r"renewable|renouvelable|renovable|rinnovabil|erneuerbar|\bred ?(?:ii|iii|2|3)\b|\bres\b(?= deployment| directive| target)|wind (?:power|energy|farm|farms|sector|industry|action plan|package)|offshore wind|onshore wind|\bsolar\b|photovolta|\bpv\b|hydropower|hydroelectric|geotherm|\bbiofuel|\bbiomethane\b|\bbiogas\b|\bbioenergy\b|\bsaf\b|sustainable aviation fuel|refueleu", "high"),
        (r"\bwind\b", "medium"),
    ], []),
    ("grids", "Grids and networks", [
        (r"\bgrids?\b|interconnect|interconnex|réseaux? (?:électrique|de transport|de distribution)|réseau|redes? (?:eléctrica|de distribución|de transporte)|\btsos?\b|\bdsos?\b|transmission (?:system|network|grid)|distribution (?:system|network|networks|grid)|network codes?|codes de réseau|projects? of common interest|projets d'intérêt commun|\bpcis?\b|trans-european networks|\bten-e\b|congestion|grid connection|paquet réseau", "high"),
    ], []),
    ("nuclear", "Nuclear", [
        (r"nuclear|nucléaire|nucleare|nuklear|kernkraft|\bsmrs?\b|small modular reactor|euratom|uranium|\breactors?\b|fusion energy", "high"),
    ], []),
    ("carbon_pricing", "Emissions trading and carbon pricing", [
        (r"\bets ?[12]?\b|emissions? trading|carbon market|carbon pric|\bcbam\b|carbon border|quotas? (?:carbone|de co2)|derechos de emisión|emissionshandel", "high"),
    ], []),
    ("electricity_market", "Energy markets and prices", [
        (r"electricity markets?|energy markets?|power markets?|market design|\bemd\b|marché de l'électricité|mercado eléctrico|mercato elettrico|strommarkt|energy prices|electricity prices|affordable energy|ppas?\b|power purchase agreement|contracts? for difference|\bcfds?\b|wholesale", "high"),
    ], []),
    ("ccs", "Carbon capture and storage", [
        (r"\bccs\b|\bccus\b|carbon capture|co2 (?:storage|capture|transport|supply chain)|carbon storage|carbon management|captage du co2|stockage du co2", "high"),
    ], []),
    ("electrification", "Electrification and efficiency", [
        (r"electrification|électrification|electrificación|elettrificazione|elektrifizierung|heat pumps?|heating and cooling|energy efficiency|efficacité énergétique|eficiencia energética|\beed\b|\bepbd\b|energy performance of buildings|e-?mobility|electric vehicles?|mobilité électrique|charging|\bafir\b", "high"),
    ], []),
    ("energy_security", "Energy security and supply", [
        (r"security of (?:energy )?supply|energy security|supply security|sécurité d'approvisionnement|seguridad de suministro|sicurezza energetica|versorgungssicherheit|repowereu|oil stocks|strategic stocks|energy resilience|preparedness|blackout|energy imports|supplies to the eu|russian (?:gas|oil|energy)", "high"),
    ], []),
    ("oil", "Oil, refining and fuels", [
        (r"\boil\b|crude|refiner|raffin|\bpétrole|petrolio|petróleo|fuel quality|oil stocks|hydrocarbons? directive|\blow-carbon fuels|alternative fuels|e-?fuels|liquid fuels", "high"),
    ], []),
    ("climate", "Climate targets and decarbonisation", [
        (r"2040 (?:climate )?(?:target|objective)|climate (?:target|targets|law|neutrality|policy|policies|change|action)|fit for 55|fit-for-55|green deal|decarboni[sz]|net[ -]zero|cible climatique|politique climatique|changement climatique|descarbonizaci|decarbonizzazione|dekarbonisierung|carbon removals?|effort sharing|post-2030 climate|climate and energy|low-carbon technolog", "high"),
    ], []),
    ("industry", "Industry and competitiveness", [
        (r"clean industrial deal|\bcid\b|industrial accelerator act|\biaa\b|net[ -]zero industry act|\bnzia\b|competitiveness|compétitivité|competitividad|competitivit|wettbewerbsfähigkeit|industrial (?:policy|strategy|decarboni[sz]ation)|politique industrielle|state aid|\bcisaf\b|\bgber\b|simplification|omnibus|critical raw material|manufacturing|\\bindustr(?:y|ies|ial)\\b", "high"),
    ], []),
    ("transport", "Transport and vehicles", [
        (r"co2 standards|cars? and vans|heavy[ -]duty|\bhdvs?\b|vehicles?|corporate fleet|fleets?|automotive|aviation|maritime|shipping|transport", "high"),
    ], []),
    ("sustainable_finance", "Sustainable finance and reporting", [
        (r"taxonomy|taxonomie|\bcsrd\b|\bcsddd\b|\bcs3d\b|\bsfdr\b|\besrs\b|sustainable finance|sustainability reporting|due diligence|green claims", "high"),
    ], []),
    ("digital", "Digital, data and AI", [
        (r"\bai\b|artificial intelligence|\bai act\b|data cent(?:re|er)s?|\bdata act\b|digital|numérique|cyber|\bnis ?(?:ii|2)\b|quantum|\bhpc\b|cloud", "high"),
    ], []),
]

_COMPILED = [(tid, name, [(re.compile(p, re.I), c) for p, c in rules], [re.compile(x, re.I) for x in excl])
             for tid, name, rules, excl in THEMES]


def clean(text) -> str:
    """Remove Excel line-break codes and collapse white space."""
    if not isinstance(text, str):
        return ""
    return re.sub(r"\s+", " ", text.replace("_x000D_", " ")).strip()


def tag(text: str) -> list[dict]:
    """Return one dict per theme found: theme id, name, matched words, confidence and a short snippet."""
    text = clean(text)
    found = []
    for tid, name, rules, excl in _COMPILED:
        t = text
        for x in excl:
            t = x.sub(" ", t)
        for rx, conf in rules:
            m = rx.search(t)
            if m:
                a, b = max(0, m.start() - 60), min(len(t), m.end() + 60)
                found.append({"theme": tid, "name": name, "matched": m.group(0).strip(), "confidence": conf,
                              "snippet": ("…" if a else "") + t[a:b].strip() + ("…" if b < len(t) else "")})
                break
    return found
