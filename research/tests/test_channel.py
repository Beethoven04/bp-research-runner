from research import channel as C


def test_opening_hours_is_store():
    html = "<p>Unser Laden</p><h3>Öffnungszeiten</h3><p>Mo-Fr 9:00 - 18:30 Uhr, Sa 10-14 Uhr</p>"
    assert C.decide({"u": html}, False)[0] == C.STORE


def test_phone_hours_not_store():
    html = "<p>Telefonischer Kundenservice Öffnungszeiten: Mo-Fr 9-17 Uhr</p>"
    assert C.decide({"u": html}, False)[0] == C.UNKNOWN


def test_address_alone_not_store():
    html = "<p>Impressum: Musterstraße 1, 10115 Berlin</p>"
    assert C.decide({"u": html}, False)[0] == C.UNKNOWN


def test_cart_and_osm_is_both():
    html = '<a href="/warenkorb">Warenkorb</a>'
    assert C.decide({"u": html}, True)[0] == C.BOTH


def test_cartier_is_not_cart():
    assert C.decide({"u": "<p>Cartier perfume, Kassa</p>"}, False)[0] == C.UNKNOWN


def test_platform_is_online():
    html = '<script src="/bundles/storefront/x.js"></script>'
    assert C.decide({"u": html}, False)[0] == C.ONLINE


def test_osm_only_is_store():
    assert C.decide({}, True)[0] == C.STORE
