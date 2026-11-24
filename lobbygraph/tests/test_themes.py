from lobbygraph.themes import tag, clean


def themes(text):
    return {t["theme"] for t in tag(text)}


def test_basic_themes():
    assert themes("Revision of the EU ETS") == {"carbon_pricing"}
    assert "hydrogen" in themes("EU hydrogen policy")
    assert {"gas", "renewables"} <= themes("LNG, SAF and power generation")
    assert "nuclear" in themes("présentation du programme nucléaire d'EDF")
    assert "grids" in themes("Congestion in electricity grids")


def test_greenhouse_gas_is_not_gas():
    assert "gas" not in themes("Reducing greenhouse gas emissions")
    assert "gas" not in themes("F-Gas")


def test_clean_and_evidence():
    assert clean("Energy prices_x000D_ Methane Regulation") == "Energy prices Methane Regulation"
    t = tag("Energy prices_x000D_ Methane Regulation")
    gas = [x for x in t if x["theme"] == "gas"][0]
    assert gas["matched"] == "Methane Regulation" and "Methane" in gas["snippet"]


def test_untagged():
    assert tag("Introductory meeting") == []
