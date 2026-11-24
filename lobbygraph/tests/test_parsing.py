from lobbygraph.parsing import parse_band, parse_intermediaries, parse_memberships, find_exclusions, normalise


def test_band():
    assert parse_band("3 500 000 - 3 999 999") == (3500000, 3999999)
    assert parse_band("<10 000") == (0, 9999)
    assert parse_band(">= 1 000 000") == (1000000, None)
    assert parse_band("213326") == (213326, 213326)
    assert parse_band("...") == (None, None)


def test_intermediaries():
    out = parse_intermediaries("Agora Strategy Group AG (25 000 - 49 999)   Flint Europe (25 000 - 49 999)   NOVE (25 000 - 49 999)")
    assert [o["name"] for o in out] == ["Agora Strategy Group AG", "Flint Europe", "NOVE"]
    assert out[0]["low"] == 25000


def test_memberships_list_and_sentence():
    text = "The most relevant memberships at EU level are (in alphabetical order):\n•    Bitkom\n•    ORGALIM"
    assert parse_memberships(text) == ["Bitkom", "ORGALIM"]
    text2 = "The most important ones at EU level are: COCIR, MedTechEurope, DigitalEurope. "
    assert parse_memberships(text2) == ["COCIR", "MedTechEurope", "DigitalEurope"]


def test_exclusions():
    t = ("relate only to Siemens AG and its consolidated subsidiaries with the exception of Siemens Healthineers AG "
         "which is separately listed in the register under the ID 982823533509-58")
    assert find_exclusions(t) == ["982823533509-58"]


def test_normalise():
    assert normalise("VDMA e.V.") == "vdma"
    assert normalise("Orgalim – Europe's Technology Industries") == "orgalim europe s technology industries"
