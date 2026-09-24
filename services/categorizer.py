"""
Transaction categoriser for BBVA movements.

For each movement the first layer that answers wins:

  1. User rules (learned from corrections): longest matching pattern wins.
  2. Credits only: income keywords (nómina, transferencias, Bizum...) → Ingresos.
  3. Built-in keyword rules: whole-word matching, longest keyword wins, ties
     go to the category listed first.
  4. Merchant category code (MCC), when a movement carries one (bank feeds
     do; Excel exports don't).
  5. Fallback: credits → Ingresos; debits → Otros, flagged for review.

The sign of the amount always matters: a debit is never "Ingresos", and a
credit that matches a spending category is a refund that reduces it.

Keyword syntax (keywords are compared against normalised text: uppercase,
no accents, single spaces):
  - "MERCADONA"  whole word; words of 5+ letters also match their plural
                 ("SEGURO" matches "SEGUROS").
  - "PSICOLOG*"  prefix: matches PSICOLOGO, PSICOLOGIA...
  - "*EATS"      a leading non-alphanumeric character is literal, so this
                 matches "UBER*EATS" and "UBER *EATS".
  - "~TIENDA"    weak: generic words that only count when no other keyword
                 matches ("COMERCIO ELECTRONICO AMAZON" is Amazon, not Compras).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Mapping, cast

INCOME = "Ingresos"
UNCATEGORIZED = "Otros"
ADJUSTMENTS = "Ajustes de cuenta"

# Where a category came from. "user" is set by hand on a single movement and
# is never overwritten; "none" means nothing matched and it needs review.
SOURCE_USER = "user"
SOURCE_RULE = "rule"
SOURCE_INCOME = "income"
SOURCE_KEYWORD = "keyword"
SOURCE_MCC = "mcc"
SOURCE_DEFAULT = "default"
SOURCE_NONE = "none"

RULES: list[tuple[str, list[str]]] = [
    # Fixed
    ("Alquiler", [
        "ALQUILER", "ARRENDAMIENTO", "COMUNIDAD DE PROPIETARIOS", "COMUNIDAD PROPIETARIOS",
    ]),
    ("Suministros", [
        "OCTOPUS", "NATURGY", "IBERDROLA", "ENDESA", "GAS NATURAL",
        "UNION FENOSA", "FENOSA", "ACCIONA ENERGIA", "HOLALUZ", "AUDAX",
        "FACTOR ENERGIA", "PODO", "EDP", "REPSOL LUZ", "CEPSA LUZ", "TOTALENERGIES", "LUCERA",
        "CANAL DE ISABEL", "CANAL ISABEL", "AGUAS DE", "AQUALIA", "HIDRAQUA", "EMASAGRA",
        "EMASA", "AIGUES DE", "EMPRESA MUNICIPAL AGUAS",
    ]),
    ("Telefonía", [
        "DIGI", "DIGI MOBIL", "MOVISTAR", "VODAFONE", "ORANGE", "YOIGO", "MASMOVIL",
        "PEPEPHONE", "SIMYO", "AMENA", "JAZZTEL", "LOWI", "FINETWORK",
        "EUSKALTEL", "R CABLE", "TELECABLE",
    ]),

    # Supermarkets
    ("Supermercado", [
        "DIA", "SUPERMERCADOS DIA", "TIENDAS DIA", "TIENDA DIA", "GRUPO DIA", "DIA MAXI", "DIA RETAIL",
        "MERCADONA", "ALCAMPO", "LIDL", "CARREFOUR", "CARREFOUR EXPRESS",
        "AHORRA MAS", "AHORRAMAS", "AHORRAMAX", "ALDI", "EROSKI", "CONSUM",
        "BM SUPERMERCADOS", "SUPERCOR", "EL ARBOL", "SUMA SUPERMERCADOS",
        "COVIRAN", "FROIZ", "GADIS", "SPAR", "MASYMAS", "SIMPLY", "SIMPLY MARKET",
        "HIPERCOR", "CONDIS", "VERITAS", "ECOVERITAS", "EL CORTE INGLES ALIMENT*",
        "MAKRO", "COSTCO", "SUPERMERCADO*", "HIPERMERCADO*",
        "FRUTERIA*", "VERDULERIA*", "CARNICERIA*", "PESCADERIA*",
        "COLMADO", "ULTRAMARINOS", "ALIMENTACION*",
    ]),

    # Delivery
    ("Delivery", [
        "GLOVO", "JUSTEAT", "JUST EAT", "UBER EATS", "UBEREATS",
        "UBER*EATS", "UBER *EATS", "*EATS", "BOLT FOOD",
        "DELIVEROO", "DOMINOS", "DOMINO'S", "TELEPIZZA", "PIZZA HUT",
    ]),

    # Restaurants & bars
    ("Restaurantes", [
        "RESTAURANTE*", "RESTAURANT", "CAFETERIA*",
        "CERVECERIA*", "TABERNA*", "MARISQUERIA*", "GASTROBAR",
        "ASADOR*", "BRASERIA*", "PIZZERIA*", "SUSHI", "KEBAB", "RAMEN", "TAPAS", "TAPERIA*",
        "HAMBURGUESERIA*", "BOCATERIA*",
        "MCDONALDS", "MC DONALDS", "MCDONALD", "MCDONALD'S", "BURGER KING", "BURGUER KING",
        "KFC", "FIVE GUYS", "TACO BELL", "SUBWAY", "PANS & COMPANY",
        "100 MONTADITOS", "TGB", "THE GOOD BURGER", "VIPS", "FOSTER'S",
        "FOSTERS HOLLYWOOD", "TGIFRIDAYS", "TGI FRIDAY*", "POPEYES",
        "FRESCO CO", "HONEST GREENS", "LATERAL", "GINOS", "WAGAMAMA",
        "BIKI BAT", "LA CUADRA",
        "BAR", "PUB", "TASCA", "MESON", "BODEGA*", "SIDRERIA*",
    ]),

    # Cafés, bakeries, ice cream, snacks on the go
    ("Cafés y Snacks", [
        "STARBUCKS", "COSTA COFFEE", "MCCAFE", "DUNKIN", "TIM HORTONS",
        "CAFES", "CAFE", "CAFETIN", "GRANIER", "RODILLA", "TAHONA",
        "PASTELERIA*", "CONFITERIA*", "BOLLERIA*", "CROISSANTERIA*",
        "PANADERIA*", "HELADERIA*", "GELATERIA*",
        "CHURRERIA*", "CHOCOLATERIA*", "~GRANJA", "HORCHATERIA*",
    ]),

    # Online shopping
    ("Amazon/Online", [
        "AMAZON", "AMZN", "ALIEXPRESS", "ALI EXPRESS", "TEMU", "MIRAVIA",
        "SHEIN", "ASOS", "ZALANDO", "PCCOMPONENTES",
        "EBAY", "WALLAPOP", "VINTED", "PAYPAL",
        "APPLE.COM", "GOOGLE PLAY", "MICROSOFT STORE", "MICROSOFT 365",
    ]),

    # Entertainment & subscriptions
    ("Ocio/Cultura", [
        "NETFLIX", "SPOTIFY", "HBO", "HBO MAX", "DISNEY", "AMAZON PRIME", "PRIME VIDEO", "APPLE TV",
        "YOUTUBE", "TWITCH", "DAZN", "FILMIN", "MOVISTAR PLUS", "MOVISTAR+", "SKYSHOWTIME",
        "AUDIBLE", "STEAM", "PLAYSTATION", "XBOX", "NINTENDO", "EPIC GAMES",
        "TICKETMASTER", "FEVER", "EVENTBRITE", "TAQUILLA*", "~ENTRADAS",
        "CINESA", "VUE CINEMAS", "YELMO", "KINEPOLIS", "CINES", "CINE",
        "TEATRO*", "MUSEO*", "FNAC TICKET*", "FNAC ESPECTACULOS",
        "APPLE.COM/BILL", "ICLOUD",
        "ESCAPE ROOM", "BOWLING", "KARTING", "~LASER", "PAINTBALL",
        "~PARQUE", "ZOO", "AQUARIUM", "ACUARIO",
    ]),

    # Car costs (parking, tolls, maintenance)
    ("Coche", [
        "PARKING*", "APARCAMIENTO*", "SABA", "EMPARK", "INDIGO PARK", "EASYPARK", "TELPARK",
        "AUTOPISTA*", "PEAJE*", "ITINERE", "CINTRA", "ABERTIS",
        "ITV", "TALLER*", "MECANICO*", "NEUMATICO*", "NORAUTO", "MIDAS", "FEU VERT",
    ]),

    # Public transport
    ("Transporte público", [
        "METRO", "METRO DE MADRID", "EMT", "MUNICIPALES BUS", "CRTM",
        "CONSORCIO REGIONAL DE TRANSPORTES", "ABONO TRANSPORTE", "TARJETA TRANSPORTE",
        "RENFE", "CERCANIAS", "ALVIA", "AVE", "AVANT", "OUIGO", "IRYO", "TMB", "FGC",
        "ALSA", "AVANZA", "FLIXBUS",
        "BICIMAD", "DONKEY REPUBLIC",
    ]),

    # Private hire
    ("Transporte privado", [
        "CABIFY", "UBER", "BOLT", "FREE NOW", "FREENOW", "MYTAXI",
        "BLABLACAR", "TAXI*", "RADIO TAXI", "TELETAXI",
    ]),

    # Health
    ("Salud", [
        "FARMACIA*", "PARAFARMACIA*",
        "CLINICA*", "HOSPITAL*", "CENTRO MEDICO", "DENTISTA*", "~DENTAL",
        "OPTICA*", "MULTIOPTICAS", "GENERAL OPTICA", "VISILAB", "ALAIN AFFLELOU",
        "SANITAS", "ADESLAS", "ASISA", "MAPFRE SALUD", "CIGNA", "DKV",
        "QUIRONSALUD", "HM HOSPITALES", "RUBER", "VITHAS",
        "FISIO*", "PSICOLOG*", "OSTEOPAT*", "PODOLOG*",
        "LABORATORIO*", "RADIOLOGIA", "ANALISIS CLINICO*",
        "GINECOLOG*", "PEDIATR*", "DERMATOLOG*",
    ]),

    # Clothing
    ("Ropa/Accesorios", [
        "ZARA", "H&M", "HM", "MANGO", "PULL&BEAR", "PULL & BEAR",
        "BERSHKA", "STRADIVARIUS", "MASSIMO DUTTI", "OYSHO",
        "PRIMARK", "LEFTIES", "UNIQLO", "SPRINGFIELD", "CORTEFIEL",
        "PEDRO DEL HIERRO", "BOSS", "TOMMY", "LEVI'S", "LEVIS", "KIABI", "DESIGUAL",
        "DECATHLON", "SPRINTER", "NIKE", "ADIDAS", "PUMA", "REEBOK", "NEW BALANCE",
        "FOOT LOCKER", "JD SPORTS", "CALZEDONIA", "INTIMISSIMI", "TEZENIS", "PARFOIS",
        "EL CORTE INGLES MODA",
    ]),

    # Beauty & personal care
    ("Belleza", [
        "SEPHORA", "DOUGLAS", "DRUNI", "PRIMOR", "NOTINO",
        "KIKO", "RITUALS", "LUSH", "THE BODY SHOP", "MAQUILLAJE",
        "PELUQUERIA*", "BARBERIA*", "BARBER",
        "SALON DE BELLEZA", "CENTRO DE ESTETICA",
        "~ESTETICA", "MANICURA", "DEPILACION*",
        "CENTROS UNIQUE", "ARENAL PERFUMERIAS", "PERFUMERIA*",
    ]),

    # Travel & accommodation (including flights)
    ("Viajes", [
        "BOOKING.COM", "BOOKING", "AIRBNB", "HOTELS.COM", "EXPEDIA", "EDREAMS",
        "TRIVAGO", "HOSTELWORLD", "LOGITRAVEL", "TRAVELGENIO", "DESTINIA",
        "NH HOTEL*", "MELIA", "BARCELO", "VINCCI", "IBIS", "NOVOTEL",
        "MARRIOTT", "HILTON", "HYATT", "AC HOTEL*", "HOTEL*", "HOSTAL*",
        "CIVITATIS", "GETYOURGUIDE", "VIATOR", "AGENCIA DE VIAJES", "VIAJES EL CORTE INGLES",
        "ALOJAMIENTO*", "APARTAMENTO TURISTICO", "APARTAMENTOS TURISTICOS",
        "AENA", "VUELING", "IBERIA", "RYANAIR", "EASYJET", "WIZZ AIR",
        "NORWEGIAN", "TRANSAVIA", "VOLOTEA", "AIR EUROPA", "BINTER",
    ]),

    # General purchases: physical stores not covered by other categories
    ("Compras", [
        "EL CORTE INGLES", "FNAC", "WORTEN", "MEDIAMARKT", "MEDIA MARKT",
        "PHONE HOUSE", "POWERPLANET", "APPLE STORE", "FLYING TIGER", "MINISO",
        "PAPELERIA*", "LIBRERIA*", "CASA DEL LIBRO",
        "JUGUETERIA*", "JUGUETES",
        "BAZAR*", "TODO A", "~NORMAL", "~ACTION",
        "~TIENDA", "~COMERCIO",
    ]),

    # Home & hardware
    ("Hogar", [
        "IKEA", "LEROY MERLIN", "BRICOMART", "BRICO DEPOT", "BRICODEPOT", "BAUHAUS", "BRICOR",
        "CONFORAMA", "MAISONS DU MONDE", "ZARA HOME", "H&M HOME", "JYSK",
        "FERRETERIA*", "FLORISTERIA*", "VIVERO*",
    ]),

    # Insurance
    ("Seguros", [
        "MAPFRE", "GENERALI", "ALLIANZ", "AXA", "MUTUA MADRILENA",
        "ZURICH", "LINEA DIRECTA", "VERTI", "CASER", "OCASO", "REALE",
        "FIATC", "SANTALUCIA", "SANTA LUCIA", "PELAYO", "HELVETIA",
        "SEGURO*", "PRIMA SEGURO",
    ]),

    # Fuel
    ("Gasolinera", [
        "REPSOL", "BP", "CEPSA", "MOEVE", "SHELL", "GALP", "CAMPSA", "PETRONOR",
        "PLENOIL", "BALLENOIL", "PETROPRIX", "GASOLINERA*", "ESTACION DE SERVICIO",
        "GASOLINA", "CARBURANTE*",
    ]),

    # Cash withdrawals
    ("Efectivo", [
        "CAJERO*", "REINTEGRO*", "RETIRADA DE EFECTIVO", "RETIRADA EFECTIVO",
        "DISPOSICION EFECTIVO", "DISPOSICION DE EFECTIVO", "ATM", "~EFECTIVO",
    ]),

    # Bank fees
    ("Comisiones", [
        "COMISION*", "MANTENIMIENTO CUENTA", "MANTENIMIENTO DE CUENTA", "CUOTA TARJETA",
        "CUOTA ANUAL TARJETA", "INTERESES DEUDORES", "GASTOS ADMINISTRACION", "DESCUBIERTO",
    ]),

    # Internal account adjustments / shared-expense reimbursements
    (ADJUSTMENTS, [
        "AJUSTE*", "LIQUIDACION", "REEMBOLSO", "COMPENSACION", "CUADRE",
    ]),
]

# Only looked at for credits (money coming in).
INCOME_KEYWORDS: list[str] = [
    "NOMINA", "TRANSF*", "BIZUM", "INGRESO*", "ABONO", "INTERESES", "INTERESES ACREEDORES",
    "PENSION", "PRESTACION", "SUBSIDIO", "AEAT", "HACIENDA",
]

# Merchant category codes (ISO 18245) that the bank may send with card payments.
MCC_CATEGORIES: dict[str, str] = {
    "5411": "Supermercado", "5422": "Supermercado", "5451": "Supermercado", "5499": "Supermercado",
    "5441": "Cafés y Snacks", "5462": "Cafés y Snacks",
    "5812": "Restaurantes", "5813": "Restaurantes", "5814": "Restaurantes",
    "5541": "Gasolinera", "5542": "Gasolinera", "5983": "Gasolinera",
    "4111": "Transporte público", "4112": "Transporte público", "4131": "Transporte público",
    "4121": "Transporte privado",
    "4511": "Viajes", "4722": "Viajes", "7011": "Viajes",
    "7523": "Coche", "4784": "Coche", "7538": "Coche", "5533": "Coche", "7542": "Coche",
    "5912": "Salud", "8011": "Salud", "8021": "Salud", "8031": "Salud", "8041": "Salud",
    "8042": "Salud", "8043": "Salud", "8049": "Salud", "8050": "Salud", "8062": "Salud",
    "8071": "Salud", "8099": "Salud",
    "5611": "Ropa/Accesorios", "5621": "Ropa/Accesorios", "5641": "Ropa/Accesorios",
    "5651": "Ropa/Accesorios", "5655": "Ropa/Accesorios", "5661": "Ropa/Accesorios",
    "5691": "Ropa/Accesorios", "5699": "Ropa/Accesorios", "5941": "Ropa/Accesorios",
    "5977": "Belleza", "7230": "Belleza", "7298": "Belleza",
    "5200": "Hogar", "5211": "Hogar", "5251": "Hogar", "5261": "Hogar", "5712": "Hogar",
    "5719": "Hogar", "5992": "Hogar",
    "4812": "Telefonía", "4814": "Telefonía",
    "4900": "Suministros",
    "6300": "Seguros",
    "6010": "Efectivo", "6011": "Efectivo",
    "4899": "Ocio/Cultura", "5735": "Ocio/Cultura", "5815": "Ocio/Cultura", "5816": "Ocio/Cultura",
    "5817": "Ocio/Cultura", "5818": "Ocio/Cultura", "7832": "Ocio/Cultura", "7922": "Ocio/Cultura",
    "7929": "Ocio/Cultura", "7991": "Ocio/Cultura", "7996": "Ocio/Cultura", "7997": "Ocio/Cultura",
    "7999": "Ocio/Cultura",
    "5311": "Compras", "5331": "Compras", "5399": "Compras", "5732": "Compras", "5734": "Compras",
    "5942": "Compras", "5943": "Compras", "5945": "Compras", "5999": "Compras",
    "5964": "Amazon/Online", "5965": "Amazon/Online", "5966": "Amazon/Online",
    "5967": "Amazon/Online", "5968": "Amazon/Online", "5969": "Amazon/Online",
}

SPENDING_CATEGORIES: list[str] = [cat for cat, _ in RULES]
# Everything a movement can be assigned to, in the order shown in forms.
CATEGORIES: list[str] = SPENDING_CATEGORIES + [INCOME, UNCATEGORIZED]


def normalize(text: str | None) -> str:
    """Uppercase, strip accents and collapse whitespace so keywords compare reliably."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.upper().split())


# ── Keyword matching ─────────────────────────────────────────────────────────

_TOKEN = re.compile(r"[A-Z0-9]+")


@dataclass(frozen=True)
class _Keyword:
    category: str
    core: str
    prefix: bool
    weak: bool
    order: int
    pattern: re.Pattern[str]

    @property
    def rank(self) -> tuple[bool, int, int]:
        # Strong beats weak, then longer beats shorter, then earlier category wins.
        return (not self.weak, len(self.core), -self.order)


def _compile_keyword(keyword: str, category: str, order: int) -> _Keyword:
    weak = keyword.startswith("~")
    keyword = keyword.lstrip("~")
    prefix = len(keyword) > 1 and keyword.endswith("*") and keyword[-2].isalnum()
    core = normalize(keyword[:-1] if prefix else keyword)
    pattern = re.escape(core)
    if core[0].isalnum():
        pattern = r"(?<![A-Z0-9])" + pattern
    if not prefix and core[-1].isalnum():
        plural = r"(?:S|ES)?" if len(core) >= 5 and core[-1].isalpha() else ""
        pattern += plural + r"(?![A-Z0-9])"
    return _Keyword(category, core, prefix, weak, order, re.compile(pattern))


class _KeywordIndex:
    """Finds the longest keyword in a text without testing every keyword."""

    def __init__(self, table: Iterable[tuple[str, list[str]]]):
        self._by_token: dict[str, list[_Keyword]] = {}
        self._prefixes: list[_Keyword] = []
        seen: set[str] = set()
        for order, (category, keywords) in enumerate(table):
            for raw in keywords:
                kw = _compile_keyword(raw, category, order)
                if kw.core + ("*" if kw.prefix else "") in seen:
                    continue  # identical keyword in an earlier category wins the tie
                seen.add(kw.core + ("*" if kw.prefix else ""))
                tokens = _TOKEN.findall(kw.core)
                if kw.prefix and len(tokens) == 1:
                    self._prefixes.append(kw)
                else:
                    self._by_token.setdefault(tokens[0], []).append(kw)

    def best(self, text: str) -> _Keyword | None:
        tokens = set(_TOKEN.findall(text))
        candidates: list[_Keyword] = []
        for token in tokens:
            candidates.extend(self._by_token.get(token, ()))
            if token.endswith("ES"):
                candidates.extend(self._by_token.get(token[:-2], ()))
            if token.endswith("S"):
                candidates.extend(self._by_token.get(token[:-1], ()))
        for kw in self._prefixes:
            if any(t.startswith(kw.core) for t in tokens):
                candidates.append(kw)
        best: _Keyword | None = None
        for kw in candidates:
            if not kw.pattern.search(text):
                continue
            if best is None or kw.rank > best.rank:
                best = kw
        return best


_SPENDING_INDEX = _KeywordIndex(RULES)
_INCOME_INDEX = _KeywordIndex([(INCOME, INCOME_KEYWORDS)])


# ── Classification ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Classification:
    category: str
    source: str

    @property
    def needs_review(self) -> bool:
        return self.source == SOURCE_NONE


RuleTuple = tuple[tuple[str, str], ...]


def compile_rules(rules: Iterable[Mapping[str, str]] | None) -> RuleTuple:
    """Turn stored user rules into a hashable, normalised form (longest first)."""
    if not rules:
        return ()
    pairs = {
        (normalize(r.get("pattern")), r.get("category") or UNCATEGORIZED)
        for r in rules
        if normalize(r.get("pattern"))
    }
    return tuple(sorted(pairs, key=lambda p: (-len(p[0]), p[0])))


def classify(
    concept: str,
    amount: float,
    rules: Iterable[Mapping[str, str]] | RuleTuple | None = None,
    *,
    counterparty: str | None = None,
    details: str | None = None,
    mcc: str | None = None,
    merchant: str | None = None,
) -> Classification:
    """Decide the category of one movement. See the module docstring for the order."""
    if isinstance(rules, tuple) and all(isinstance(r, tuple) for r in rules):
        compiled = cast(RuleTuple, rules)  # already compiled
    else:
        compiled = compile_rules(cast("Iterable[Mapping[str, str]] | None", rules))
    haystack = normalize(" ".join(p for p in (concept, counterparty, details) if p))
    # Rules are also checked against the cleaned merchant name, because that is
    # what Revisar suggests as the pattern (it may drop words from the middle).
    merchant_text = normalize(merchant or merchant_key(concept, counterparty))
    return _classify(haystack, amount >= 0, compiled, (mcc or "").strip(), merchant_text)


@lru_cache(maxsize=8192)
def _classify(haystack: str, is_credit: bool, rules: RuleTuple, mcc: str, merchant: str = "") -> Classification:
    for pattern, category in rules:
        if pattern in haystack or pattern in merchant:
            if category == INCOME and not is_credit:
                continue  # a debit can never be income
            return Classification(category, SOURCE_RULE)

    if is_credit and _INCOME_INDEX.best(haystack):
        return Classification(INCOME, SOURCE_INCOME)

    keyword = _SPENDING_INDEX.best(haystack)
    if keyword:
        return Classification(keyword.category, SOURCE_KEYWORD)

    mcc_category = MCC_CATEGORIES.get(mcc)
    if mcc_category:
        return Classification(mcc_category, SOURCE_MCC)

    if is_credit:
        return Classification(INCOME, SOURCE_DEFAULT)
    return Classification(UNCATEGORIZED, SOURCE_NONE)


def categorize(
    concept: str,
    custom_rules: Iterable[Mapping[str, str]] | None = None,
    amount: float = -1.0,
) -> str:
    """Category for a concept string. Treated as a debit unless an amount says otherwise."""
    return classify(concept, amount, custom_rules).category


# ── Merchant names ───────────────────────────────────────────────────────────

# Card-payment wording in front of the merchant name. Bizum and transfer
# prefixes are kept on purpose: "BIZUM ENVIADO A ROCIO" is a better rule
# pattern than just "ROCIO".
_NOISE_PREFIXES = [
    "COMPRA INTERNET EN ", "COMPRA EN ", "COMPRA ",
    "PAGO CON TARJETA EN ", "PAGO CON TARJETA ", "PAGO EN ", "PAGO A ", "PAGO ",
    "RECIBO ", "ADEUDO ", "CARGO A ", "CARGO ", "DOMICILIACION ", "TPVIRTUAL ",
]

_PLACE_WORDS = {
    "MADRID", "BARCELONA", "VALENCIA", "SEVILLA", "ZARAGOZA", "MALAGA", "MURCIA",
    "PALMA", "BILBAO", "ALICANTE", "CORDOBA", "VALLADOLID", "VIGO", "GIJON",
    "GRANADA", "OVIEDO", "SANTANDER", "PAMPLONA", "SALAMANCA", "TOLEDO",
    "ALCALA", "GETAFE", "LEGANES", "ALCORCON", "MOSTOLES", "FUENLABRADA",
    "ES", "ESP", "SPAIN", "ESPANA",
}


def merchant_key(concept: str, counterparty: str | None = None) -> str:
    """
    A short, stable name for the merchant, used to group movements for review
    and as the suggested pattern for a new rule.
    "COMPRA EN MERCADONA MADRID 20/09" → "MERCADONA"; "Bolt.eu/o/2405151234" → "BOLT.EU".
    """
    raw = counterparty or concept or ""
    upper = unicodedata.normalize("NFKD", raw)
    upper = "".join(ch for ch in upper if not unicodedata.combining(ch)).upper().strip()

    # Cut trailing noise: location after a separator or a run of spaces, and
    # BBVA's ", CON LA TARJETA : 4940XXXXXXXX1234 EL 2026-09-20" tail.
    upper = re.split(r"\s[-/|]\s|\s{2,}|,?\s+CON LA TARJETA\b", upper)[0]
    text = " ".join(upper.split())

    for prefix in _NOISE_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):]
            break

    text = re.sub(r"HTTPS?://", "", text)
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", text)                    # ISO dates
    text = re.sub(r"\b\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?\b", " ", text)  # dates, before cutting paths
    text = re.sub(r"(?<=[A-Z0-9])/\S*", "", text)                  # BOLT.EU/O/123 → BOLT.EU
    text = re.sub(r"[*X]{3,}\d+|\b\d{4,}\b", " ", text)              # card masks, references
    words = text.split()
    while len(words) > 1 and (words[-1].strip(".,") in _PLACE_WORDS or len(words[-1]) == 1):
        words.pop()
    key = " ".join(words).strip(" .,-")
    return key or normalize(concept)[:40]


def suggest_pattern(concept: str) -> str:
    """Suggested rule pattern for a concept (kept for older callers)."""
    return merchant_key(concept)


def category_for_mcc(mcc: str | None) -> str | None:
    return MCC_CATEGORIES.get((mcc or "").strip())
