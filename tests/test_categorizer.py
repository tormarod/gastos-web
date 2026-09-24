import pytest

from services.categorizer import (
    INCOME,
    SOURCE_DEFAULT,
    SOURCE_INCOME,
    SOURCE_KEYWORD,
    SOURCE_MCC,
    SOURCE_NONE,
    SOURCE_RULE,
    UNCATEGORIZED,
    categorize,
    classify,
    compile_rules,
    merchant_key,
    normalize,
)


@pytest.mark.parametrize("concept, amount, expected", [
    # Bugs in the previous version
    ("REINTEGRO CAJERO BBVA C/ ALCALA", -50.0, "Efectivo"),
    ("RECARGA ABONO TRANSPORTE", -21.8, "Transporte público"),
    ("AMAZON DIGITAL SERVICES", -4.99, "Amazon/Online"),
    ("COMPRA EN MEDIA MARKT ALCALA", -129.0, "Compras"),
    ("ZARA HOME MADRID", -39.9, "Hogar"),
    ("MOVISTAR PLUS+", -9.99, "Ocio/Cultura"),
    ("EL CORTE INGLES MODA", -60.0, "Ropa/Accesorios"),
    ("APPLE.COM/BILL", -2.99, "Ocio/Cultura"),
    ("SEGUROS BILBAO", -30.0, "Seguros"),
    # Formats seen in real BBVA exports
    ("UBER *EATS", -18.0, "Delivery"),
    ("UBER*EATS", -18.0, "Delivery"),
    ("UBER *TRIP", -9.0, "Transporte privado"),
    ("Bolt.eu/o/2405151234", -7.5, "Transporte privado"),
    ("BOLT FOOD", -15.0, "Delivery"),
    ("Mercadona Valencia", -45.3, "Supermercado"),
    # Longest / most specific keyword wins
    ("PAYPAL *NETFLIX", -12.99, "Ocio/Cultura"),
    ("HM HOSPITALES", -80.0, "Salud"),
    ("CAFETERIA LA PAZ", -6.0, "Restaurantes"),
    ("CAFE DE ORIENTE", -4.0, "Cafés y Snacks"),
    ("COMERCIO ELECTRONICO AMAZON EU", -30.0, "Amazon/Online"),
    ("COMPRA EN DIA 1234 MADRID", -23.1, "Supermercado"),
    # Prefixes, plurals and accents
    ("PSICOLOGA MARTA RUIZ", -60.0, "Salud"),
    ("Cafetería Ñandú", -3.0, "Restaurantes"),
    ("CAJEROS BBVA", -20.0, "Efectivo"),
])
def test_keywords(concept, amount, expected):
    result = classify(concept, amount)
    assert result.category == expected
    assert result.source == SOURCE_KEYWORD


def test_keywords_only_match_whole_words():
    assert classify("MEDIODIA TAPAS BAR", -30).category == "Restaurantes"  # not "DIA"
    assert classify("DIGITALIZACION SL", -30).category == UNCATEGORIZED    # not "DIGI"
    assert classify("NAVE INDUSTRIAL 3", -30).category == UNCATEGORIZED    # not "AVE"


def test_a_debit_is_never_income():
    assert classify("NOMINA", -100).category == UNCATEGORIZED
    assert classify("TRANSFERENCIA RECIBIDA", -10).category == UNCATEGORIZED


def test_credits():
    assert classify("TRANSFERENCIA RECIBIDA DE RODRIGO", 1000).category == INCOME
    assert classify("NOMINA SEPTIEMBRE", 2100).source == SOURCE_INCOME
    assert classify("LIQUIDACION INTERESES", 0.12).category == INCOME
    # A refund lands in its spending category (and will reduce it)
    assert classify("DEVOLUCION AMAZON", 25).category == "Amazon/Online"
    unknown = classify("ALGO DESCONOCIDO", 5)
    assert (unknown.category, unknown.source) == (INCOME, SOURCE_DEFAULT)
    assert not unknown.needs_review


def test_unknown_debits_need_review():
    result = classify("COSA RARA SL", -5)
    assert (result.category, result.source) == (UNCATEGORIZED, SOURCE_NONE)
    assert result.needs_review
    assert classify("BIZUM ENVIADO A PEPE", -20).needs_review


def test_user_rules_win_and_the_longest_pattern_applies():
    rules = [
        {"pattern": "biki", "category": "Ocio/Cultura"},
        {"pattern": "BIKI BAT", "category": "Restaurantes"},
        {"pattern": "MERCADONA", "category": "Hogar"},
    ]
    assert classify("BIKI BAT MADRID", -30, rules) == classify("BIKI BAT", -1, compile_rules(rules))
    assert classify("BIKI BAT MADRID", -30, rules).category == "Restaurantes"
    assert classify("BIKINI SHOP", -30, rules).category == "Ocio/Cultura"
    mercadona = classify("MERCADONA VALENCIA", -10, rules)
    assert (mercadona.category, mercadona.source) == ("Hogar", SOURCE_RULE)


def test_a_rule_to_income_is_ignored_for_debits():
    rules = [{"pattern": "BIZUM", "category": INCOME}]
    assert classify("BIZUM RECIBIDO DE ANA", 20, rules).category == INCOME
    assert classify("BIZUM ENVIADO A ANA", -20, rules).category == UNCATEGORIZED


def test_merchant_category_code_fills_gaps():
    result = classify("PANIFICADORA PEREZ", -3.2, mcc="5462")
    assert (result.category, result.source) == ("Cafés y Snacks", SOURCE_MCC)
    assert classify("PANIFICADORA PEREZ", -3.2, mcc="9999").category == UNCATEGORIZED
    # Keywords are closer to your categories than the bank's codes
    assert classify("MERCADONA", -5, mcc="5812").category == "Supermercado"


def test_counterparty_and_details_are_used():
    assert classify("PAGO 1234", -9, counterparty="NETFLIX INTERNATIONAL").category == "Ocio/Cultura"
    assert classify("Rodrigo", 900, details="Transferencia recibida").category == INCOME


def test_categorize_keeps_the_old_signature():
    assert categorize("NETFLIX.COM") == "Ocio/Cultura"
    assert categorize("BIKI BAT", [{"pattern": "biki", "category": "Restaurantes"}]) == "Restaurantes"
    assert categorize("NOMINA", amount=1500) == INCOME


@pytest.mark.parametrize("concept, expected", [
    ("COMPRA EN MERCADONA MADRID 20/09", "MERCADONA"),
    ("Bolt.eu/o/2405151234", "BOLT.EU"),
    ("PAGO CON TARJETA EN LA TAHONA DE CHAMBERI", "LA TAHONA DE CHAMBERI"),
    ("REINTEGRO CAJERO BBVA C/ ALCALA", "REINTEGRO CAJERO BBVA"),
    ("BIZUM ENVIADO A PEPE", "BIZUM ENVIADO A PEPE"),
    ("AMAZON EU  LUXEMBOURG", "AMAZON EU"),
    ("TARJ ****1234 STARBUCKS", "TARJ STARBUCKS"),
])
def test_merchant_key(concept, expected):
    assert merchant_key(concept) == expected


def test_bbva_card_payment_wording():
    concept = "PAGO CON TARJETA EN MERCADONA, CON LA TARJETA : 4940XXXXXXXX1234 EL 2026-09-20"
    assert merchant_key(concept) == "MERCADONA"


def test_the_suggested_pattern_always_matches_its_own_movements():
    # The suggestion drops noise from the middle; the rule must still apply.
    concept = "COMPRA TIENDA 1234567 LA ESQUINA"
    pattern = merchant_key(concept)
    assert pattern == "TIENDA LA ESQUINA"
    result = classify(concept, -10, [{"pattern": pattern, "category": "Hogar"}])
    assert (result.category, result.source) == ("Hogar", SOURCE_RULE)


def test_merchant_key_prefers_the_counterparty():
    assert merchant_key("PAGO 99887766", counterparty="Netflix International") == "NETFLIX INTERNATIONAL"


def test_normalize():
    assert normalize("  Cafetería   ñandú ") == "CAFETERIA NANDU"
    assert normalize(None) == ""
