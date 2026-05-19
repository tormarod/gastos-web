"""
Keyword-based transaction categorizer for BBVA statements.
Matches Spanish bank concept strings to spending categories.
"""

RULES: list[tuple[str, list[str]]] = [
    ("Alquiler",        ["ALQUILER", "ARRENDAMIENTO"]),
    ("Suministros",     ["OCTOPUS", "NATURGY", "IBERDROLA", "ENDESA", "GAS NATURAL", "UNION FENOSA"]),
    ("Telefonía",       ["DIGI", "MOVISTAR", "VODAFONE", "ORANGE", "YOIGO", "MASMOVIL"]),
    ("Supermercado",    ["DIA ", "SUPERMERCADOS DIA", "MERCADONA", "ALCAMPO", "LIDL", "CARREFOUR",
                         "AHORRA MAS", "ALDI", "EROSKI", "CONSUM", "EL CORTE INGLES ALIMENT"]),
    ("Delivery",        ["GLOVO", "JUSTEAT", "JUST EAT", "UBER EATS", "UBEREATS", "DELIVEROO",
                         "DOMINOS", "DOMINO'S", "TELEPIZZA"]),
    ("Restaurantes",    ["RESTAURANTE", "CAFETERIA", "CAFETERÍA", "BAR ", "BIKI BAT", "LA CUADRA",
                         "MCDONALDS", "MC DONALDS", "BURGER KING", "KFC", "FIVE GUYS",
                         "VIPS", "TGIFRIDAYS", "FOSTER'S", "FOSTERSS", "CERVECERIA",
                         "CERVECERÍA", "TABERNA", "MARISQUERIA", "MARISQUERÍA"]),
    ("Amazon/Online",   ["AMAZON", "AMZN", "ALIEXPRESS", "SHEIN", "ZARA ONLINE", "MANGO ONLINE",
                         "EL CORTE INGLES", "PCCOMPONENTES", "MEDIAMARKT ONLINE"]),
    ("Ocio/Cultura",    ["FEVER", "EVENTBRITE", "TICKETMASTER", "CINESA", "VUE CINEMAS", "YELMO",
                         "STEAM", "PLAYSTATION", "SPOTIFY", "NETFLIX", "HBO", "DISNEY",
                         "PRIME VIDEO", "APPLE.COM/BILL"]),
    ("Transporte",      ["METRO ", "RENFE", "EMT ", "CABIFY", "UBER", "BOLT", "FREE NOW",
                         "BLABLACAR", "AENA", "VUELING", "IBERIA", "RYANAIR", "EASYJET"]),
    ("Salud",           ["FARMACIA", "CLINICA", "CLÍNICA", "DENTISTA", "OPTICA", "ÓPTICA",
                         "SEGURO SALUD", "SANITAS", "ADESLAS", "ASISA"]),
    ("Ropa/Accesorios", ["ZARA", "H&M", "HM ", "MANGO", "PULL&BEAR", "BERSHKA", "STRADIVARIUS",
                         "MASSIMO DUTTI", "PRIMARK", "DECATHLON", "NIKE", "ADIDAS"]),
    ("Ingresos",        ["NOMINA", "NÓMINA", "TRANSFERENCIA RECIBIDA", "BIZUM RECIBIDO", "INGRESO"]),
]

_LOWER_RULES = [(cat, [kw.lower() for kw in kws]) for cat, kws in RULES]


def categorize(concept: str) -> str:
    c = concept.lower()
    for cat, keywords in _LOWER_RULES:
        if any(kw in c for kw in keywords):
            return cat
    return "Otros"
