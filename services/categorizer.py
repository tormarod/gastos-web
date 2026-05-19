"""
Transaction categorizer for BBVA statements.

Built-in keyword rules cover common Spanish merchants.
Custom rules (saved to S3) are applied first and always win.
"""

from __future__ import annotations

import re

RULES: list[tuple[str, list[str]]] = [
    # Ingresos first — prevents salary transfers being tagged as expenses
    ("Ingresos", [
        "NOMINA", "NÓMINA", "TRANSFERENCIA RECIBIDA", "BIZUM RECIBIDO",
        "INGRESO EN EFECTIVO", "DEVOLUCION ", "DEVOLUCIÓN", "REINTEGRO",
        "ABONO ", "LIQUIDACION INTERESES", "INTERESES ACREEDORES",
    ]),

    # Fixed
    ("Alquiler", [
        "ALQUILER", "ARRENDAMIENTO", "COMUNIDAD DE PROPIETARIOS",
    ]),
    ("Suministros", [
        "OCTOPUS", "NATURGY", "IBERDROLA", "ENDESA", "GAS NATURAL",
        "UNION FENOSA", "FENOSA", "ACCIONA ENERGIA", "HOLALUZ", "AUDAX",
        "FACTOR ENERGIA", "PODO ", "EDP ", "REPSOL LUZ", "CEPSA LUZ",
        "CANAL DE ISABEL", "AGUAS DE", "AQUALIA", "HIDRAQUA", "EMASAGRA",
        "EMASA", "AIGUES DE", "EMPRESA MUNICIPAL AGUAS",
    ]),
    ("Telefonía", [
        "DIGI", "MOVISTAR", "VODAFONE", "ORANGE", "YOIGO", "MASMOVIL",
        "PEPEPHONE", "SIMYO", "AMENA", "JAZZTEL", "LOWI", "FINETWORK",
        "EUSKALTEL", "R CABLE", "TELECABLE",
    ]),

    # Supermarkets
    ("Supermercado", [
        "DIA ", "SUPERMERCADOS DIA", "TIENDAS DIA", "DIA,S.A", "GRUPO DIA",
        "MERCADONA", "ALCAMPO", "LIDL", "CARREFOUR", "CARREFOUR EXPRESS",
        "AHORRA MAS", "AHORRAMAX", "ALDI", "EROSKI", "CONSUM",
        "BM SUPERMERCADOS", "SUPERCOR", "EL ARBOL", "SUMA SUPERMERCADOS",
        "COVIRAN", "COVIRÁN", "FROIZ", "GADIS", "SPAR ", "MASYMAS",
        "SIMPLY MARKET", "DIA MAXI", "HIPERCOR", "CONDIS", "VERITAS",
        "ECOVERITAS", "EL CORTE INGLES ALIMENT", "MAKRO", "COSTCO",
        "SUPERMERCADO", "FRUTERIA", "FRUTERÍA", "VERDULERIA", "VERDULERÍA",
        "CARNICERIA", "CARNICERÍA", "PESCADERIA", "PESCADERÍA", "PANADERIA",
        "PANADERÍA", "COLMADO", "ULTRAMARINOS",
    ]),

    # Delivery
    ("Delivery", [
        "GLOVO", "JUSTEAT", "JUST EAT", "UBER EATS", "UBEREATS",
        "UBER*EATS", "UBER *EATS",
        "DELIVEROO", "DOMINOS", "DOMINO'S", "TELEPIZZA", "PIZZA HUT",
    ]),

    # Restaurants & cafes
    ("Restaurantes", [
        "RESTAURANTE", "CAFETERIA", "CAFETERÍA",
        "CERVECERIA", "CERVECERÍA", "TABERNA", "MARISQUERIA", "MARISQUERÍA",
        "ASADOR", "BRASERIA", "BRASERÍA", "PIZZERIA", "PIZZERÍA",
        "HAMBURGUESERIA", "HAMBURGUESERÍA", "BOCATERIA", "BOCATERÍA",
        "MCDONALDS", "MC DONALDS", "MCDONALD", "BURGER KING", "BURGUER KING",
        "KFC ", "FIVE GUYS", "TACO BELL", "SUBWAY ", "PANS & COMPANY",
        "100 MONTADITOS", "TGB ", "THE GOOD BURGER", "VIPS ", "FOSTER'S",
        "FOSTERS HOLLYWOOD", "TGIFRIDAYS", "TGI FRIDAY", "POPEYES",
        "FRESCO CO", "HONEST GREENS", "LATERAL ", "GINOS ", "WAGAMAMA",
        "BIKI BAT", "LA CUADRA",
        "BAR ", "PUB ", "TASCA ", "MESÓN", "MESON ", "BODEGA ",
        "SIDRERIA", "SIDRERRÍA",
    ]),

    # Cafes, bakeries, ice cream, snacks on the go
    ("Cafés y Snacks", [
        "STARBUCKS", "COSTA COFFEE", "MCCAFE", "DUNKIN",
        "CAFES ", "CAFE ", "CAFETÍN",
        "PASTELERIA", "PASTELERÍA", "CONFITERIA", "CONFITERÍA",
        "BOLLERIA", "BOLLERÍA", "CROISSANTERIA", "CROISSANTERÍA",
        "PANADERIA", "PANADERÍA",
        "HELADERIA", "HELADERÍA", "GELATERIA", "GELATERÍA",
        "CHURRERIA", "CHURRERÍA", "CHOCOLATERIA", "CHOCOLATERÍA",
        "GRANJA ", "HORCHATERIA", "HORCHATERRÍA",
    ]),

    # Online shopping
    ("Amazon/Online", [
        "AMAZON", "AMZN",
        "ALIEXPRESS", "ALI EXPRESS",
        "SHEIN", "ASOS ", "ZALANDO",
        "EL CORTE INGLES", "FNAC ", "PCCOMPONENTES", "MEDIAMARKT",
        "PHONE HOUSE", "WORTEN", "POWERPLANET",
        "EBAY", "WALLAPOP", "VINTED",
        "PAYPAL",
        "APPLE.COM", "GOOGLE PLAY", "MICROSOFT STORE", "MICROSOFT 365",
    ]),

    # Entertainment & subscriptions
    ("Ocio/Cultura", [
        "NETFLIX", "SPOTIFY", "HBO ", "DISNEY", "AMAZON PRIME", "APPLE TV",
        "YOUTUBE", "TWITCH", "DAZN ", "FILMIN", "MOVISTAR PLUS", "SKYSHOWTIME",
        "STEAM ", "PLAYSTATION", "XBOX ", "NINTENDO", "EPIC GAMES",
        "TICKETMASTER", "FEVER ", "EVENTBRITE", "TAQUILLA",
        "CINESA", "VUE CINEMAS", "YELMO", "KINEPOLIS", "CINES ",
        "TEATRO ", "MUSEO ", "FNAC TICKET", "FNAC ESPECTACULOS",
        "APPLE.COM/BILL", "ICLOUD",
        "ESCAPE ROOM", "BOWLING", "KARTING", "LASER ", "PAINTBALL",
        "PARQUE ", "ZOO ", "AQUARIUM",
    ]),

    # Car costs (parking, tolls, maintenance)
    ("Coche", [
        "PARKING ", "SABA ", "EMPARK", "INDIGO PARK",
        "AUTOPISTA", "PEAJE ", "ITINERE", "CINTRA", "ABERTIS",
        "ITV ", "TALLER ", "MECANICO", "MECÁNICO", "NEUMATICO", "NEUMÁTICO",
    ]),

    # Public transport
    ("Transporte público", [
        "METRO ", "METRO DE MADRID", "EMT ", "MUNICIPALES BUS",
        "RENFE", "CERCANIAS", "CERCANÍAS", "ALVIA ", "AVE ", "AVANT ",
        "ALSA ", "AVANZA ", "FLIXBUS",
        "BICIMAD", "DONKEY REPUBLIC",
    ]),

    # Private hire
    ("Transporte privado", [
        "CABIFY", "UBER ", "BOLT", "FREE NOW", "FREENOW", "MYTAXI",
        "BLABLACAR",
    ]),

    # Health
    ("Salud", [
        "FARMACIA", "PARAFARMACIA",
        "CLINICA", "CLÍNICA", "HOSPITAL", "CENTRO MEDICO", "CENTRO MÉDICO",
        "DENTISTA", "DENTAL ", "CLINICA DENTAL",
        "OPTICA", "ÓPTICA", "MULTIÓPTICAS", "GENERAL OPTICA", "VISILAB",
        "SANITAS", "ADESLAS", "ASISA ", "MAPFRE SALUD", "CIGNA ",
        "QUIRONSALUD", "HM HOSPITALES", "RUBER ",
        "FISIO", "FISIOTERAPIA", "PSICOLOG",
        "LABORATORIO", "RADIOLOGIA", "ANALISIS CLINICO",
        "GINECOLOG", "PEDIATR", "DERMATOLOG",
    ]),

    # Clothing
    ("Ropa/Accesorios", [
        "ZARA ", "H&M", "HM ", "MANGO ", "PULL&BEAR", "PULL & BEAR",
        "BERSHKA", "STRADIVARIUS", "MASSIMO DUTTI", "OYSHO ",
        "PRIMARK", "LEFTIES", "UNIQLO", "SPRINGFIELD", "CORTEFIEL",
        "PEDRO DEL HIERRO", "BOSS ", "TOMMY ", "LEVI'S", "LEVIS ",
        "DECATHLON", "NIKE ", "ADIDAS ", "PUMA ", "REEBOK", "NEW BALANCE",
        "FOOT LOCKER", "JD SPORTS",
        "EL CORTE INGLES MODA",
    ]),

    # Beauty & personal care (moved out of Ropa)
    ("Belleza", [
        "SEPHORA", "DOUGLAS", "DRUNI ", "PRIMOR ", "NOTINO",
        "KIKO ", "RITUALS", "LUSH ", "THE BODY SHOP", "MAQUILLAJE",
        "PELUQUERIA", "PELUQUERÍA", "BARBERIA", "BARBERÍA",
        "SALON DE BELLEZA", "SALÓN DE BELLEZA", "CENTRO DE ESTETICA",
        "ESTETICA", "ESTÉTICA", "MANICURA", "DEPILACION", "DEPILACIÓN",
        "CENTROS UNIQUE", "ARENAL PERFUMERIAS", "PERFUMERIA", "PERFUMERÍA",
    ]),

    # Travel & accommodation (including flights)
    ("Viajes", [
        "BOOKING.COM", "BOOKING ", "AIRBNB", "HOTELS.COM", "EXPEDIA",
        "TRIVAGO", "HOSTELWORLD", "LOGITRAVEL", "TRAVELGENIO", "DESTINIA",
        "NH HOTEL", "MELIA ", "BARCELO ", "VINCCI", "IBIS ", "NOVOTEL",
        "MARRIOTT", "HILTON", "HYATT", "AC HOTEL",
        "CIVITATIS", "GETYOURGUIDE", "VIATOR", "AGENCIA DE VIAJES",
        "ALOJAMIENTO", "APARTAMENTO TURISTICO",
        "AENA", "VUELING", "IBERIA ", "RYANAIR", "EASYJET", "WIZZ AIR",
        "NORWEGIAN", "TRANSAVIA", "VOLOTEA",
    ]),

    # General purchases — physical stores not covered by other categories
    ("Compras", [
        "EL CORTE INGLES", "FNAC ", "WORTEN", "MEDIAMARKT", "PCCOMPONENTES",
        "PHONE HOUSE", "POWERPLANET",
        "PAPELERIA", "PAPELERÍA", "LIBRERIA", "LIBRERÍA", "CASA DEL LIBRO",
        "JUGUETERIA", "JUGUETERÍA", "JUGUETES",
        "BAZAR", "TODO A ", "NORMAL ", "ACTION ",
        "TIENDA ", "COMERCIO ",
    ]),

    # Home & hardware
    ("Hogar", [
        "IKEA", "LEROY MERLIN", "BRICOMART", "BAUHAUS", "BRICOR",
        "CONFORAMA", "MAISONS DU MONDE", "ZARA HOME",
        "FERRETERIA", "FERRETERÍA", "FERRETERIAS",
        "FLORISTERIA", "FLORISTERÍA", "VIVERO",
    ]),

    # Insurance
    ("Seguros", [
        "MAPFRE", "GENERALI", "ALLIANZ", "AXA ", "MUTUA MADRILENA",
        "MUTUA MADRILEÑA", "ZURICH", "LINEA DIRECTA", "VERTI ",
        "FIATC ", "SANTALUCIA", "SANTA LUCIA", "PELAYO ",
        "SEGURO ", "PRIMA SEGURO",
    ]),

    # Fuel
    ("Gasolinera", [
        "REPSOL", "BP ", "CEPSA ", "SHELL ", "GALP ", "CAMPSA",
        "PLENOIL", "BALLENOIL", "GASOLINERA", "ESTACION DE SERVICIO",
        "GASOLINA", "CARBURANTE",
    ]),

    # Cash withdrawals
    ("Efectivo", [
        "CAJERO", "REINTEGRO CAJERO", "DISPOSICION EFECTIVO", "ATM ",
        "EFECTIVO ",
    ]),

    # Bank fees
    ("Comisiones", [
        "COMISION", "COMISIÓN", "MANTENIMIENTO CUENTA", "CUOTA TARJETA",
        "INTERESES DEUDORES", "GASTOS ADMINISTRACION",
    ]),

    # Internal account adjustments / shared-expense reimbursements
    ("Ajustes de cuenta", [
        "AJUSTE", "LIQUIDACION ", "LIQUIDACIÓN ", "REEMBOLSO",
        "COMPENSACION", "COMPENSACIÓN", "CUADRE",
    ]),
]

_LOWER_RULES = [(cat, [kw.lower() for kw in kws]) for cat, kws in RULES]

# BBVA concept prefixes to strip when suggesting a pattern to the user
_BBVA_PREFIXES = [
    "COMPRA EN ", "COMPRA INTERNET EN ", "COMPRA ",
    "PAGO EN ", "PAGO A ", "PAGO CON TARJETA EN ", "PAGO ",
    "RECIBO ", "CARGO A ", "CARGO ", "DOMICILIACION ",
    "TPVIRTUAL ", "BIZUM A ", "BIZUM DE ",
    "TRANSFERENCIA A ", "TRANSFERENCIA DE ",
]


def categorize(concept: str, custom_rules: list[dict] | None = None) -> str:
    """
    Categorize a BBVA transaction concept string.
    custom_rules are applied first so user corrections always win.
    """
    c = concept.lower()

    if custom_rules:
        for rule in custom_rules:
            if rule["pattern"].lower() in c:
                return rule["category"]

    for cat, keywords in _LOWER_RULES:
        if any(kw in c for kw in keywords):
            return cat

    return "Otros"


def suggest_pattern(concept: str) -> str:
    """
    Strip common BBVA prefixes and trailing noise to suggest a clean
    merchant pattern for the user to confirm before saving.
    """
    upper = concept.upper().strip()

    for prefix in _BBVA_PREFIXES:
        if upper.startswith(prefix):
            upper = upper[len(prefix):]
            break

    # Remove trailing date patterns like "15/05/26" or "15-05-2026"
    upper = re.sub(r"\s+\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}$", "", upper).strip()

    # Remove trailing city/location noise after a dash or slash
    for sep in [" - ", " / ", "  "]:
        if sep in upper:
            upper = upper.split(sep)[0].strip()

    return upper
