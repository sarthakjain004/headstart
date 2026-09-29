"""World country gazetteer for the ``country`` Search filter (query-time alias expansion, ADR-0273).

Why this exists: the served table's ``location`` is whatever the ATS wrote, and only India was ever
structured (:mod:`headstart.search_filters.india_gazetteer`). A job in the United States is written
"United States", "USA", "US", "Remote - US", "Austin, TX" or "San Francisco, CA"; a job in Germany
is "Germany", "Berlin, DE" or "München" (measured on the 498,848-row served table, 2026-09-29).
A substring search finds one spelling at a time, so this module expands an ISO 3166-1 alpha-2
code into a match over every observed way of writing that country.

Each :class:`Country` names its places two ways, and each way in two strengths:

- a **word** matches anywhere, bounded by anything that is not a letter or digit ("berlin" in
  "Berlin, Germany" and in "Remote (Berlin)", never inside "Berliner");
- a **segment** matches only a whole part of the string between separators (:data:`_LEFT`,
  :data:`_RIGHT`): "ca" in "Toronto, ON, CA" and "us" in "Remote - US", never the "ca" in
  "Casablanca" or the "la" in "Louvain-la-Neuve". Codes are segments. So are names whose word
  form is a trap: "mexico" is a segment, so "Albuquerque, New Mexico" (segment "new mexico") is
  not Mexico, while "Guadalajara, Mexico" is.
- **sure** terms name the country whatever else the row says;
- **shared** terms also name another place: "CA" is California and Canada, "HR" is Haryana and
  Croatia, "(fr)" is a language tag, "London" is also in Ontario, "Dublin" is also in Ohio. A
  shared term counts only when the row names no other country for sure, and a shared *word*
  also yields to another country's shared segment, so "Vienna, VA" is Virginia and not Austria,
  and "Dublin, OH" is Ohio and not Ireland. Every two-letter code but "us" is shared. The cost
  is a multi-country row that names this country only through a shared term ("Dublin;
  Toronto" is Canada only); ADR-0273 measures it.
- a **city** word is a city whose name a smaller place in another country shares: "berlin" is
  also Berlin, CT and New Berlin, WI. It counts unless the row states another country, by its
  English name or one of its codes, so "Berlin, CT" and "New Berlin, Wisconsin, United States"
  are not Germany. Unlike a shared word it never yields to another country's city, so "Berlin;
  Montreal" is Germany and Canada (ADR-0322).

No term belongs to two countries (``test_no_term_names_two_countries``): a term both claimed
would put its bare rows in both.

India is not compiled here. ``country=IN`` is the India filter's whole-country rule
(:mod:`headstart.search_filters.india_filter`), so the two filters can never disagree; India's
names still enter every other country's guard (:data:`INDIA`), so "Chennai, TN, India" is not
Tennessee.

Every term is a trusted constant, lowercase, free of quotes and of ``%``: nothing a request
sends is ever interpolated here beyond the dict lookup in :func:`where`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache

from headstart.search_filters import india_gazetteer


@dataclass(frozen=True)
class Country:
    """One country's names. See the module docstring for what the four kinds mean."""

    name: str
    words: tuple[str, ...]
    segments: tuple[str, ...] = ()
    shared_words: tuple[str, ...] = ()
    shared_segments: tuple[str, ...] = ()
    city_words: tuple[str, ...] = ()


# fmt: off
#: Countries by the number of served Jobs that name them (measured 2026-09-29), the order the
#: website and the MCP tool list them in. India is added by
#: :data:`headstart.search_filters.country_filter.CODES`. A country here was named by at least
#: 50 served Jobs; below that the rows are mostly lists of every country a remote job allows.
COUNTRIES: dict[str, Country] = {
    "US": Country(
        "United States",
        words=(
            "united states", "usa", "u.s.a", "u.s.", "us remote", "remote us", "remote in us",
            "nyc", "bay area", "afb", "sfb",
            # how a remote job is written when it names the country and no place (ADR-0343);
            # "the us" alone would take "Outside the US"
            "in the us", "within the us", "in us", "us only", "us-based", "us based",
            "united-states", "unites states", "united state",
            "alabama", "alaska", "arizona", "arkansas", "colorado", "connecticut", "delaware",
            "florida", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky",
            "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota",
            "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire",
            "new jersey", "new mexico", "new york", "north carolina", "north dakota", "ohio",
            "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
            "south dakota", "tennessee", "texas", "utah", "vermont", "virginia", "washington",
            "west virginia", "wisconsin", "wyoming", "district of columbia",
            "san francisco", "seattle", "austin", "chicago", "los angeles", "san diego",
            "santa clara", "sunnyvale", "palo alto", "mountain view", "cupertino", "redmond",
            "bellevue", "atlanta", "dallas", "houston", "denver", "charlotte", "mclean",
            "reston", "herndon", "chantilly", "annapolis junction", "fort meade", "huntsville",
            "plano", "irving", "jersey city", "nashville", "tampa", "pittsburgh",
            "philadelphia", "raleigh", "boise", "indianapolis", "fort wayne", "minneapolis",
            "salt lake city", "miami", "orlando", "las vegas", "san antonio", "detroit",
            "baltimore", "cincinnati", "st. louis", "saint louis", "kansas city", "milwaukee",
            "san mateo", "el segundo", "costa mesa", "irvine", "fremont", "milpitas",
            "hillsboro", "beaverton", "colorado springs", "boulder", "fort worth", "littleton",
            "alpharetta", "bethesda", "rockville", "ashburn", "menlo park", "foster city",
            "redwood city", "santa monica", "oakland", "berkeley", "sacramento", "pasadena",
            "torrance", "carlsbad", "long beach", "pleasanton", "burlingame", "scottsdale",
            "tempe", "chandler", "tucson", "albuquerque", "omaha", "des moines",
            "oklahoma city", "tulsa", "memphis", "louisville", "new orleans", "jacksonville",
            "fort lauderdale", "ann arbor", "grand rapids", "knoxville", "columbus",
            "cleveland", "dayton", "buffalo", "hartford", "providence", "boston", "brooklyn",
            "manhattan", "frisco", "richardson", "arlington", "fairfax", "falls church",
            "manassas", "wilmington", "newark", "princeton", "king of prussia", "waltham",
            "springfield", "honolulu", "anchorage", "spokane", "tacoma", "hawthorne",
            "palm bay", "fort belvoir", "quantico", "dahlgren", "silicon valley",
            "morrisville", "lexington", "charleston", "savannah", "dearborn", "cary",
            "laurel", "englewood", "centennial", "broomfield", "grand prairie", "palmdale",
            "marietta", "moorestown", "owego", "cape canaveral", "oak brook", "goleta",
            "piscataway", "highlands ranch", "sioux falls", "newbury park", "west lafayette",
            "boca raton", "tallahassee", "kennedy space center", "titusville", "bentonville",
            "wichita", "greenville", "madison", "middletown", "auburn hills",
            "racine", "troy", "helena", "patuxent river", "white sands", "peoria", "longmont",
            "mckinney", "modesto", "whitsett", "canonsburg", "ocala", "woburn", "youngstown",
            "rocky hill", "denton", "muscatine", "winsted", "boxborough", "fayetteville",
            "fort smith", "seal beach", "brookshire", "kings bay", "lufkin", "harrisburg",
            "jefferson city", "cedar rapids", "riviera beach", "fargo", "chattanooga", "yuma",
            "issaquah", "franklin park", "st louis", "lehi", "pryor", "fort george g meade",
            "indianola", "archbald", "courtland", "az_mesa", "muscatatuck", "hurlburt field",
            "fort bragg",
        ),
        segments=("us", "u.s", "california", "calif"),
        shared_words=(
            "california", "georgia", "san jose", "durham", "rochester", "westminster",
            "aurora", "kirkland", "phoenix", "elgin", "andover",
        ),
        # Every state and DC as its two-letter code. "in" and "de" are absent: "Bangalore, IN"
        # (457 rows) and "Berlin, DE" outnumber Indiana and Delaware written that way, and
        # those states' rows nearly all carry "US" or "United States" as well.
        shared_segments=(
            "al", "ak", "az", "ar", "ca", "co", "ct", "fl", "ga", "hi", "id", "il", "ia", "ks",
            "ky", "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj",
            "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut",
            "vt", "va", "wa", "wv", "wi", "wy", "dc",
        ),
    ),
    "GB": Country(
        "United Kingdom",
        words=(
            "united kingdom", "great britain", "england", "scotland", "northern ireland", "uk",
            "u.k.", "greater london", "city of london", "glasgow", "belfast", "cardiff",
            "sheffield", "nottingham", "newcastle upon tyne", "milton keynes", "warrington",
            "coventry", "leicester", "swindon", "guildford", "basingstoke", "bracknell",
            "slough", "farnborough", "stevenage", "crawley", "dundee", "greater manchester",
            "west midlands", "cambridgeshire", "lanarkshire", "yorkshire", "lancashire",
            "berkshire", "hertfordshire", "oxfordshire", "buckinghamshire", "tyne and wear",
            "stoke-on-trent", "woking", "havant", "bridgwater", "gaydon", "barrow-in-furness",
            "ampthill", "warton", "sizewell",
            "solihull", "rochdale", "luton", "gosport", "bicester", "motherwell",
            "castleford", "bedfordshire", "chester, cheshire", "bury st edmunds", "broadoak",
            "new malden", "alderley edge", "scotstoun", "great baddow",
        ),
        segments=("gbr", "wales"),
        shared_words=(
            "london", "manchester", "birmingham", "bristol", "edinburgh", "cambridge",
            "oxford", "reading", "newcastle", "liverpool", "southampton", "derby", "brighton",
            "aberdeen", "surrey", "wales", "camden", "plymouth", "leeds",
            "cheltenham", "ipswich", "bradford",
        ),
        shared_segments=("gb",),
    ),
    "CA": Country(
        "Canada",
        words=(
            "canada", "quebec", "québec", "british columbia", "alberta", "manitoba",
            "saskatchewan", "nova scotia", "newfoundland", "prince edward island", "yukon",
            "nunavut", "northwest territories", "toronto", "montreal", "montréal", "calgary",
            "edmonton", "mississauga", "markham", "winnipeg", "burnaby", "brampton",
            "oakville", "kitchener", "saskatoon", "kanata", "dorval", "richmond hill",
            "north york", "guelph", "gatineau", "quebec city", "moncton", "fredericton",
            "thornhill", "vaughan", "etobicoke", "sherbrooke", "boucherville", "brossard",
            "kelowna", "lethbridge", "barrie", "chalk river", "charlottetown", "yellowknife",
            "st laurent", "nepean",
            # "ottawa" alone is shared (Ottawa, IL); beside the province, or Canada's code,
            # it is Canada whatever a state code says (ADR-0343)
            "ottawa, ontario", "ottawa, ca",
        ),
        # A province code followed by "CA" is Canada whatever else the row says: this is what
        # keeps "Vancouver, BC, CA" out of California.
        segments=(
            "can", "on, ca", "bc, ca", "qc, ca", "ab, ca", "mb, ca", "sk, ca", "ns, ca",
            "nb, ca", "nl, ca", "pe, ca", "nt, ca", "nu, ca", "yt, ca",
            # SuccessFactors cuts a province to four letters (ADR-0343)
            "onta, ca", "brit, ca", "nova, ca", "queb, ca", "albe, ca", "mani, ca",
            "sask, ca", "newf, ca",
        ),
        shared_words=(
            "ontario", "vancouver", "waterloo", "halifax", "laval", "burlington", "dartmouth",
            "windsor", "kingston", "regina", "scarborough", "new brunswick", "ottawa",
        ),
        shared_segments=("on", "bc", "ab", "qc", "mb", "ns", "nb", "pei", "nu", "yt"),
    ),
    "SG": Country(
        "Singapore", words=("singapore", "braddell"), segments=("sgp",), shared_segments=("sg",)
    ),
    "DE": Country(
        "Germany",
        words=(
            "germany", "deutschland", "bavaria", "bayern", "baden-württemberg",
            "baden-wurttemberg", "baden-wuerttemberg", "hesse", "hessen",
            "north rhine-westphalia", "north rhine westphalia", "nordrhein-westfalen",
            "lower saxony", "niedersachsen", "saxony", "sachsen", "thuringia", "thüringen",
            "rhineland-palatinate", "rheinland-pfalz", "schleswig-holstein",
            # Berlin with its own state or code after it, which no other country's code can
            # take from it; "berlin" alone is a city word (ADR-0322).
            "berlin, berlin", "berlin, be", "berlin, de",
            "munich", "münchen", "muenchen", "frankfurt", "cologne", "köln", "koeln",
            "stuttgart", "düsseldorf", "dusseldorf", "duesseldorf", "dresden", "leipzig",
            "nuremberg", "nürnberg", "nuernberg", "hannover", "karlsruhe", "mannheim",
            "heidelberg", "bonn", "darmstadt", "dortmund", "essen", "aachen", "walldorf",
            "wiesbaden", "mainz", "ingolstadt", "wolfsburg", "erlangen", "jena", "ulm",
            "freiburg", "potsdam", "regensburg", "augsburg", "kiel", "bochum", "münster",
            "duisburg", "metzingen", "braunschweig", "göttingen", "bielefeld", "ratingen",
            "eschborn", "bad homburg", "garching", "böblingen", "sindelfingen", "neckarsulm",
            "oberpfaffenhofen",
            "hanau", "ismaning", "friedrichshafen", "remscheid", "neuss", "weiterstadt",
            "ludwigsburg", "bensheim", "melsungen", "hennigsdorf", "sankt augustin",
            "bad mergentheim", "pfaffenhofen an der ilm", "bitterfeld wolfen", "schönaich",
        ),
        segments=("deu",),
        # Also Berlin, CT, New Berlin, WI and Berlin, NJ (ADR-0322).
        city_words=("berlin",),
        shared_words=("hamburg", "hanover", "bremen"),
        shared_segments=("de",),
    ),
    "CN": Country(
        "China",
        words=(
            "shanghai", "beijing", "shenzhen", "suzhou", "guangzhou", "hangzhou", "chengdu",
            "nanjing", "wuhan", "wuxi", "hefei", "xiamen", "chongqing", "tianjin", "dalian",
            "qingdao", "jinan", "changsha", "dongguan", "changzhou", "taicang", "foshan",
            "guangdong", "jiangsu", "zhejiang", "sichuan", "shandong", "fujian", "anhui",
            "hubei", "shaanxi", "mainland china",
            "zhuhai", "kunshan", "zhangjiagang", "深圳", "西安", "中国",
        ),
        segments=("china", "chn", "prc"),
        shared_words=("china",),
        shared_segments=("cn",),
    ),
    "AU": Country(
        "Australia",
        words=(
            # Perth with Western Australia's code: "perth" alone is shared (ADR-0322).
            "australia", "sydney", "perth, wa", "wa, au", "canberra", "hobart", "gold coast",
            "geelong",
            "north sydney", "new south wales", "queensland", "western australia",
            "south australia", "tasmania", "australian capital territory",
            "northern territory",
            "mawson lakes", "cooma", "enoggera",
        ),
        segments=("aus", "nsw", "vic", "qld", "tas", "act"),
        # "perth" is also Perth, Scotland and Perth Amboy, NJ (ADR-0322).
        shared_words=("melbourne", "victoria", "adelaide", "brisbane", "perth"),
        shared_segments=("au",),
    ),
    "PL": Country(
        "Poland",
        words=(
            "poland", "polska", "warszawa", "krakow", "kraków", "wroclaw", "wrocław", "gdansk",
            "gdańsk", "poznan", "poznań", "lodz", "łódź", "katowice", "gliwice", "szczecin",
            "lublin", "bydgoszcz", "rzeszow", "rzeszów", "gdynia", "masovian voivodeship",
            "mazowieckie", "lesser poland", "jasionka",
            "tajęcina",
        ),
        segments=("pol",),
        shared_words=("warsaw",),
        shared_segments=("pl",),
    ),
    "MY": Country(
        "Malaysia",
        words=(
            "malaysia", "kuala lumpur", "penang", "selangor", "petaling jaya", "cyberjaya",
            "johor", "bayan lepas", "shah alam", "subang jaya", "pulau pinang", "putrajaya",
            "kulim", "seremban", "melaka", "sepang", "bukit jalil",
        ),
        segments=("mys",),
        shared_segments=("my",),
    ),
    "PH": Country(
        "Philippines",
        words=(
            "philippines", "manila", "makati", "taguig", "quezon city", "pasig", "cebu",
            "mandaluyong", "muntinlupa", "alabang", "ortigas", "bonifacio global city",
            "cavite", "tanauan",
        ),
        segments=("phl",),
        shared_words=("laguna",),
        shared_segments=("ph",),
    ),
    "ES": Country(
        "Spain",
        words=(
            "spain", "españa", "espana", "madrid", "barcelona", "málaga", "malaga", "seville",
            "sevilla", "bilbao", "zaragoza", "catalonia", "cataluña", "catalunya", "getafe",
            "tres cantos", "las rozas", "alcobendas", "alicante", "murcia",
            "sant cugat", "rubí", "marchamalo",
        ),
        segments=("esp",),
        shared_words=("valencia", "granada", "toledo"),
        shared_segments=("es",),
    ),
    "MX": Country(
        "Mexico",
        words=(
            "méxico", "mexico city", "ciudad de mexico", "ciudad de méxico", "cdmx",
            "guadalajara", "monterrey", "jalisco", "nuevo león", "nuevo leon", "tijuana",
            "baja california", "querétaro", "queretaro", "puebla", "chihuahua", "hermosillo",
            "ciudad juárez", "ciudad juarez", "zapopan", "mexicali",
            "silao",
        ),
        segments=("mexico", "mex"),
        shared_words=("mexico",),
        shared_segments=("mx",),
    ),
    "NL": Country(
        "Netherlands",
        words=(
            "netherlands", "nederland", "amsterdam", "rotterdam", "eindhoven", "utrecht",
            "the hague", "den haag", "delft", "hoofddorp", "noord-holland", "north holland",
            "zuid-holland", "south holland", "noord-brabant", "north brabant", "gelderland",
            "groningen", "leiden", "amstelveen", "veldhoven", "nijmegen", "amersfoort",
            "papendrecht", "hengelo", "niederlande", "zoetermeer", "hilversum", "breda",
            "schiphol", "wageningen", "s hertogenbosch", "randstad",
        ),
        segments=("nld",),
        shared_words=("holland",),
        shared_segments=("nl",),
    ),
    "IE": Country(
        "Ireland",
        words=(
            "republic of ireland", "cork", "galway", "limerick", "county dublin",
            "co. dublin", "leinster", "munster", "waterford",
            "donegal", "kilkenny", "nenagh",
        ),
        segments=("ireland", "irl"),
        shared_words=("ireland", "dublin"),
        shared_segments=("ie",),
    ),
    "AE": Country(
        "United Arab Emirates",
        words=(
            "united arab emirates", "uae", "u.a.e", "dubai", "abu dhabi", "sharjah", "dubayy",
            "ras al khaimah", "ajman", "al ain", "al-ain",
        ),
        segments=("are",),
        shared_segments=("ae",),
    ),
    "TW": Country(
        "Taiwan",
        words=(
            "taiwan", "taipei", "hsinchu", "taichung", "tainan", "taoyuan", "kaohsiung",
            "new taipei", "jubei", "新竹",
            "台北", "台中", "台南", "台灣",
        ),
        segments=("twn",),
        shared_segments=("tw",),
    ),
    "JP": Country(
        "Japan",
        words=(
            "japan", "tokyo", "osaka", "yokohama", "kyoto", "nagoya", "fukuoka", "kobe",
            "hiroshima", "sapporo", "kanagawa", "aichi",
            "chiyoda-ku", "chiyoda", "shinagawa", "kagoshima", "yamagata",
        ),
        segments=("jpn",),
        shared_segments=("jp",),
    ),
    "PT": Country(
        "Portugal",
        words=("portugal", "lisbon", "lisboa", "braga", "aveiro", "coimbra", "maia"),
        segments=("prt",),
        shared_words=("porto",),
        shared_segments=("pt",),
    ),
    "RO": Country(
        "Romania",
        words=(
            "romania", "rumänien", "bucharest", "bukarest", "bucuresti", "bucurești",
            "cluj-napoca", "cluj", "timisoara", "timișoara", "iasi", "iași", "brasov", "brașov",
            "ilfov", "craiova", "sibiu", "arad",
        ),
        segments=("rou",),
        shared_segments=("ro",),
    ),
    "BR": Country(
        "Brazil",
        words=(
            "brazil", "brasil", "são paulo", "sao paulo", "rio de janeiro", "campinas",
            "belo horizonte", "curitiba", "porto alegre", "brasília", "brasilia", "recife",
            "florianópolis", "florianopolis", "minas gerais",
            "indaiatuba", "sao jose dos campos", "são josé dos campos",
        ),
        segments=("bra",),
        shared_segments=("br",),
    ),
    "EG": Country(
        "Egypt",
        words=("egypt", "cairo", "giza", "new cairo", "smart village"),
        segments=("egy",),
        shared_words=("alexandria",),
        shared_segments=("eg",),
    ),
    "SA": Country(
        "Saudi Arabia",
        words=(
            "saudi arabia", "ksa", "riyadh", "jeddah", "dammam", "al khobar", "khobar",
            "dhahran", "jubail", "makkah", "mecca", "king abdullah economic city",
        ),
        segments=("sau",),
        shared_segments=("sa",),
    ),
    "FR": Country(
        "France",
        words=(
            "france", "île-de-france", "ile-de-france", "toulouse", "grenoble", "bordeaux",
            "nantes", "lille", "marseille", "strasbourg", "sophia antipolis", "montpellier",
            "rennes", "courbevoie",
            "villeurbanne", "genas", "clichy la garenne",
        ),
        segments=("fra",),
        shared_words=("paris", "lyon"),
        shared_segments=("fr",),
    ),
    "IL": Country(
        "Israel",
        words=(
            "tel aviv", "tel-aviv", "haifa", "jerusalem", "herzliya", "raanana",
            "petah tikva", "petach tikva", "yokneam", "rehovot", "netanya", "beer sheva",
            "hod hasharon", "ramat gan", "caesarea", "kfar saba", "airport city", "or yehuda",
            "modiin", "migdal haemek",
            "ra’anana",
        ),
        # A segment, because "Beth Israel Lahey Health" is a Boston hospital.
        segments=("israel", "isr"),
        shared_words=("israel",),
    ),
    "VN": Country(
        "Vietnam",
        words=(
            "vietnam", "viet nam", "ho chi minh", "hồ chí minh", "hanoi", "ha noi", "hà nội",
            "da nang", "đà nẵng", "hcmc", "dong nai",
        ),
        segments=("vnm",),
        shared_segments=("vn",),
    ),
    "ZA": Country(
        "South Africa",
        words=(
            "south africa", "johannesburg", "cape town", "durban", "pretoria", "sandton",
            "centurion", "gauteng", "western cape", "kwazulu-natal", "stellenbosch",
            "melrose arch",
        ),
        segments=("zaf",),
        shared_segments=("za",),
    ),
    "BE": Country(
        "Belgium",
        words=(
            "belgium", "belgique", "belgië", "brussels", "bruxelles", "brussel", "antwerp",
            "antwerpen", "ghent", "gent", "leuven", "mechelen", "liège", "liege", "flanders",
            "vlaanderen", "wallonia", "vlaams gewest", "diegem", "zaventem", "charleroi",
            "louvain-la-neuve",
            "zedelgem", "ternat", "groot bijgaarden", "erembodegem",
        ),
        shared_segments=("be",),
    ),
    "IT": Country(
        "Italy",
        words=(
            "italy", "italia", "milan", "milano", "turin", "torino", "bologna", "firenze",
            "napoli", "genova", "catania", "bari", "pisa", "padova", "padua", "lombardy",
            "lombardia", "lazio", "piedmont",
            "modena", "san giovanni valdarno", "sant agata bolognese", "brugherio", "bozen",
            "bolzano", "vicenza",
        ),
        segments=("ita",),
        shared_words=("rome", "roma", "florence", "naples", "genoa"),
        shared_segments=("it",),
    ),
    "CH": Country(
        "Switzerland",
        words=(
            "switzerland", "schweiz", "suisse", "zurich", "zürich", "zuerich", "geneva",
            "genève", "geneve", "lausanne", "basel", "zug", "lugano", "winterthur",
            "st. gallen", "vaud", "aargau",
            "allschwil", "pfäffikon", "pfaffikon", "mägenwil", "näfels",
        ),
        segments=("che",),
        shared_words=("bern",),
        shared_segments=("ch",),
    ),
    "SE": Country(
        "Sweden",
        words=(
            "sweden", "sverige", "stockholm", "gothenburg", "göteborg", "goteborg", "malmö",
            "malmo", "lund", "uppsala", "solna", "linköping", "linkoping", "västerås",
            "vasteras", "kista", "ludvika",
        ),
        segments=("swe",),
        shared_segments=("se",),
    ),
    "TH": Country(
        "Thailand",
        words=(
            "thailand", "bangkok", "chon buri", "chonburi", "rayong", "phuket", "chiang mai",
            "lamphun", "กรุงเทพ",
            "samut prakan", "chachoengsao", "laem chabang",
        ),
        segments=("tha",),
        shared_segments=("th",),
    ),
    "GR": Country(
        "Greece",
        words=(
            "greece", "athina", "αθηνα", "thessaloniki", "attica", "attiki", "attikí",
            "heraklion", "patras",
            "ελλάδα", "αττική",
        ),
        segments=("grc",),
        shared_words=("athens",),
        shared_segments=("gr",),
    ),
    "CO": Country(
        "Colombia",
        words=(
            "colombia", "bogota", "bogotá", "medellin", "medellín", "barranquilla",
            "antioquia",
        ),
        segments=("col",),
        shared_words=("cartagena",),
    ),
    "HK": Country(
        "Hong Kong",
        words=("hong kong", "kowloon", "hksar", "香港"),
        segments=("hkg",),
        shared_segments=("hk",),
    ),
    "AR": Country(
        "Argentina",
        words=("argentina", "buenos aires", "caba", "rosario", "mendoza"),
        segments=("arg",),
        shared_words=("córdoba", "cordoba"),
    ),
    "PK": Country(
        "Pakistan",
        words=(
            "pakistan", "lahore", "karachi", "islamabad", "rawalpindi", "faisalabad",
            "multan", "sindh",
        ),
        segments=("pak",),
        shared_words=("punjab",),
        shared_segments=("pk",),
    ),
    "HU": Country(
        "Hungary",
        words=("hungary", "budapest", "debrecen", "szeged", "győr", "pécs"),
        segments=("hun",),
        shared_segments=("hu",),
    ),
    "BG": Country(
        "Bulgaria",
        words=("bulgaria", "sofia", "plovdiv", "varna"),
        segments=("bgr",),
        shared_segments=("bg",),
    ),
    "ID": Country(
        "Indonesia",
        words=(
            "indonesia", "jakarta", "bandung", "surabaya", "batam", "bekasi", "tangerang",
            "yogyakarta", "karawang", "makassar", "kalimantan", "gendalo gendang",
            "gendalo gandang",
            "cikarang", "cilegon",
        ),
        segments=("idn",),
        shared_words=("bali",),
    ),
    "NZ": Country(
        "New Zealand",
        words=("new zealand", "auckland", "christchurch", "waikato", "north island", "east tamaki"),
        segments=("nzl",),
        shared_words=("wellington", "hamilton", "canterbury"),
        shared_segments=("nz",),
    ),
    "NG": Country(
        "Nigeria",
        words=(
            "nigeria", "lagos", "abuja", "ikeja", "lekki", "port harcourt", "kaduna", "sokoto",
            "kebbi", "zamfara",
        ),
        segments=("nga",),
        shared_segments=("ng",),
    ),
    "UA": Country(
        "Ukraine",
        words=(
            "ukraine", "kyiv", "kiev", "lviv", "kharkiv", "dnipro", "odesa", "україна", "украина",
        ),
        segments=("ukr",),
        shared_segments=("ua",),
    ),
    "QA": Country(
        "Qatar", words=("qatar", "doha", "lusail"), segments=("qat",), shared_segments=("qa",)
    ),
    "RS": Country(
        "Serbia",
        words=("serbia", "belgrade", "beograd", "novi sad"),
        segments=("srb",),
        shared_segments=("rs",),
    ),
    "DK": Country(
        "Denmark",
        words=(
            "denmark", "danmark", "copenhagen", "københavn", "kobenhavn", "aarhus", "odense",
            "aalborg", "lyngby",
            "smørum", "ballerup",
        ),
        segments=("dnk",),
        shared_segments=("dk",),
    ),
    "CZ": Country(
        "Czechia",
        words=(
            "czechia", "czech republic", "prague", "praha", "brno", "ostrava", "plzeň",
            "mladá boleslav",
        ),
        segments=("cze",),
        shared_segments=("cz",),
    ),
    "AT": Country(
        "Austria",
        words=(
            "austria", "österreich", "wien", "graz", "linz", "salzburg", "innsbruck",
            "klagenfurt", "villach",
        ),
        segments=("aut",),
        shared_words=("vienna",),
        shared_segments=("at",),
    ),
    "CR": Country(
        "Costa Rica",
        words=("costa rica", "heredia", "alajuela"),
        segments=("cri",),
        shared_words=("san josé",),
        shared_segments=("cr",),
    ),
    "KR": Country(
        "South Korea",
        words=(
            "south korea", "korea", "seoul", "pangyo", "seongnam", "gyeonggi", "suwon",
            "hwaseong", "busan", "incheon", "cheonan", "bucheon", "anyang",
        ),
        segments=("kor",),
        shared_segments=("kr",),
    ),
    "LT": Country(
        "Lithuania",
        words=("lithuania", "vilnius", "kaunas", "klaipeda", "klaipėda"),
        segments=("ltu",),
        shared_segments=("lt",),
    ),
    "FI": Country(
        "Finland",
        words=(
            "finland", "suomi", "helsinki", "espoo", "tampere", "oulu", "vantaa", "uusimaa",
            "turku",
        ),
        segments=("fin",),
        shared_segments=("fi",),
    ),
    "NO": Country(
        "Norway",
        words=("norway", "norge", "oslo", "trondheim", "stavanger"),
        shared_words=("bergen",),
        shared_segments=("no",),
    ),
    "TR": Country(
        "Türkiye",
        words=("turkey", "türkiye", "turkiye", "istanbul", "ankara", "izmir", "bursa"),
        segments=("tur",),
        shared_segments=("tr",),
    ),
    "LK": Country(
        "Sri Lanka",
        words=(
            "sri lanka", "colombo", "cololmbo", "nugegoda", "battaramulla", "rajagiriya",
            "nawala",
            "kandy", "galle", "dehiwala", "katunayake", "mount lavinia", "avissawella", "biyagama",
            "malabe", "pannipitiya", "kelaniya", "boralesgamuwa", "maharagama", "athurugiriya",
            "seeduwa", "moratuwa", "polonnaruwa", "kotte",
        ),
        segments=("lka",),
        shared_segments=("lk",),
    ),
    "CL": Country(
        "Chile", words=("chile",), segments=("chl",), shared_words=("santiago",),
        shared_segments=("cl",),
    ),
    "PE": Country("Peru", words=("perú", "arequipa"), shared_words=("peru", "lima")),
    "LB": Country(
        "Lebanon", words=("beirut",), segments=("lbn",), shared_words=("lebanon",),
        shared_segments=("lb",),
    ),
    "LU": Country(
        "Luxembourg", words=("luxembourg",), segments=("lux",), shared_segments=("lu",)
    ),
    "KE": Country("Kenya", words=("kenya", "nairobi"), shared_segments=("ke",)),
    "CY": Country(
        "Cyprus",
        words=("cyprus", "limassol", "nicosia", "larnaca"),
        segments=("cyp",),
        shared_segments=("cy",),
    ),
    "SK": Country(
        "Slovakia",
        words=("slovakia", "slovak republic", "bratislava", "košice", "kosice", "žilina"),
        segments=("svk",),
    ),
    "MT": Country(
        "Malta",
        # "Malta" is shared: "USA - New York - Malta" is a GlobalFoundries fab in New York.
        words=(
            "valletta", "sliema", "san ġiljan", "gżira", "birkirkara", "gozo", "mosta", "mriehel",
        ),
        segments=("mlt",),
        shared_words=("malta",),
    ),
    "JO": Country(
        "Jordan", words=("amman",), segments=("jor",), shared_words=("jordan",),
        shared_segments=("jo",),
    ),
    "PR": Country(
        "Puerto Rico",
        words=("puerto rico", "aguadilla", "juncos", "humacao"),
        segments=("pri",),
        shared_words=("san juan",),
        shared_segments=("pr",),
    ),
    "UY": Country(
        "Uruguay", words=("uruguay", "montevideo"), segments=("ury",), shared_segments=("uy",)
    ),
    "EE": Country("Estonia", words=("estonia", "tallinn", "tartu"), shared_segments=("ee",)),
    "LV": Country(
        "Latvia", words=("latvia", "riga"), segments=("lva",), shared_segments=("lv",)
    ),
    "HR": Country(
        "Croatia",
        words=("croatia", "hrvatska", "zagreb", "rijeka", "osijek", "kerestinec"),
        segments=("hrv",),
        shared_segments=("hr",),
    ),
    "PA": Country(
        "Panama", words=("ciudad de panama",), segments=("panama",),
        shared_words=("panama",), shared_segments=("pan",),
    ),
    "AM": Country("Armenia", words=("armenia", "yerevan")),
    "TN": Country(
        "Tunisia", words=("tunisia", "tunis", "zaghouan", "bizerte"), segments=("tun",)
    ),
    "DO": Country("Dominican Republic", words=("dominican republic", "santo domingo")),
    # Named by 156 served Jobs on 2026-09-29; without it "Tbilisi, Georgia" was the US state
    # (ADR-0322). "georgia" stays the US's shared word, which Georgia's cities now guard.
    "GE": Country("Georgia", words=("tbilisi", "batumi", "kutaisi", "rustavi")),
    "SI": Country(
        "Slovenia",
        words=("slovenia", "ljubljana", "maribor"),
        segments=("svn",),
        shared_segments=("si",),
    ),
    "GT": Country("Guatemala", words=("guatemala",), segments=("gtm",)),
    "KZ": Country("Kazakhstan", words=("kazakhstan", "almaty", "astana"), segments=("kaz",)),
    "BO": Country("Bolivia", words=("bolivia", "cochabamba", "santa cruz de la sierra")),
    "EC": Country("Ecuador", words=("ecuador", "quito", "guayaquil"), segments=("ecu",)),
    "SV": Country("El Salvador", words=("el salvador", "san salvador"), segments=("slv",)),
    "OM": Country("Oman", words=("oman", "muscat"), segments=("omn",)),
    "MA": Country(
        "Morocco",
        words=("morocco", "casablanca", "rabat", "marrakech", "tangier", "sala al jadida"),
    ),
    "NI": Country("Nicaragua", words=("nicaragua", "managua"), segments=("nic",)),
    "AL": Country("Albania", words=("albania", "tirana"), segments=("alb",)),
    "KW": Country("Kuwait", words=("kuwait",), segments=("kwt",)),
    "BH": Country("Bahrain", words=("bahrain", "manama", "muharraq"), segments=("bhr",)),
    "MD": Country("Moldova", words=("moldova", "chisinau", "chișinău"), segments=("mda",)),
    "MU": Country("Mauritius", words=("mauritius", "ebene"), segments=("mus",)),
    "GH": Country("Ghana", words=("ghana", "accra"), segments=("gha",)),
    "BD": Country("Bangladesh", words=("bangladesh", "dhaka"), segments=("bgd",)),
    "HN": Country(
        "Honduras", words=("honduras", "tegucigalpa", "san pedro sula"), segments=("hnd",)
    ),
    "PY": Country("Paraguay", words=("paraguay", "asunción", "asuncion"), segments=("pry",)),
    "BA": Country(
        "Bosnia and Herzegovina", words=("bosnia", "sarajevo", "banja luka"),
        segments=("bih",),
    ),
    "MK": Country("North Macedonia", words=("north macedonia", "skopje"), segments=("mkd",)),
    "TZ": Country("Tanzania", words=("tanzania", "dar es salaam"), segments=("tza",)),
    # ADR-0343: named by fewer than ADR-0273's 50 Jobs, each by at least 15 that named no country.
    "GY": Country("Guyana", words=("guyana",)),
    "SR": Country(
        "Suriname", words=("suriname", "paramaribo", "brokopondo", "nieuw nickerie")
    ),
    "IQ": Country(
        "Iraq",
        words=("iraq", "baghdad", "basra", "erbil", "mosul"),
        shared_segments=("iq",),
    ),
    "DZ": Country(
        "Algeria",
        words=("algeria", "algiers", "hassi messaoud"),
        shared_segments=("dz",),
    ),
    "XK": Country(
        "Kosovo",
        words=("kosovo", "pristina", "prishtina", "prishtine"),
        shared_segments=("xk",),
    ),
    "NP": Country("Nepal", words=("nepal", "kathmandu", "bagmati", "pokhara")),
    "ZM": Country("Zambia", words=("zambia", "lusaka", "kalumbila", "solwezi")),
    "UG": Country("Uganda", words=("uganda", "kampala")),
    "BZ": Country("Belize", words=("belize", "belmopan")),
    "KH": Country(
        "Cambodia",
        words=("cambodia", "phnom penh", "siem reap"),
        shared_segments=("kh",),
    ),
    "IS": Country(
        "Iceland",
        words=("iceland", "reykjavik", "reykjavík", "keflavik", "keflavík", "reykjanesbaer"),
    ),
    "BY": Country("Belarus", words=("belarus", "minsk")),
    "RW": Country("Rwanda", words=("rwanda", "kigali"), shared_segments=("rw",)),
    "ZW": Country("Zimbabwe", words=("zimbabwe", "harare", "bulawayo")),
    "UZ": Country("Uzbekistan", words=("uzbekistan", "tashkent"), shared_segments=("uz",)),
    "KY": Country("Cayman Islands", words=("cayman islands", "grand cayman", "cayman")),
    "MV": Country("Maldives", words=("maldives", "hulhule", "kaafu")),
    "CI": Country(
        "Ivory Coast",
        words=("ivory coast", "ivoire", "divoire", "abidjan", "yamoussoukro"),
    ),
    "LY": Country("Libya", words=("libya", "benghazi", "tobruk")),
    "SY": Country("Syria", words=("syria", "aleppo", "latakia")),
    "MM": Country("Myanmar", words=("myanmar", "yangon", "rangoon", "naypyidaw")),
    "MG": Country("Madagascar", words=("madagascar", "antananarivo", "toamasina")),
    "ET": Country("Ethiopia", words=("ethiopia", "addis ababa")),
    "CM": Country("Cameroon", words=("cameroon", "douala", "yaoundé", "yaounde")),
    "BS": Country("Bahamas", words=("bahamas", "grand bahama")),
    "TO": Country("Tonga", words=("tonga", "tongatapu")),
    "BN": Country("Brunei", words=("brunei", "bandar seri begawan")),
    "PG": Country("Papua New Guinea", words=("papua new guinea", "port moresby")),
    "TT": Country("Trinidad and Tobago", words=("trinidad and tobago", "trinidad & tobago")),
    "LI": Country("Liechtenstein", words=("liechtenstein", "vaduz")),
    # "Jamaica, NY" is Queens: the bare name is shared, so a state code takes it, and Kingston
    # (also Kingston, Ontario) is Jamaica's only beside "Jamaica" or its parish.
    "JM": Country(
        "Jamaica",
        words=("kingston, jamaica", "kingston 10", "kingston, saint andrew", "montego bay"),
        shared_words=("jamaica",),
    ),
    # The West Bank's own names: "Al-Bireh" would be read as a hyphenated "AL", Alabama.
    "PS": Country(
        "Palestine",
        words=(
            "palestine", "palestinian territory", "palestinian territories", "ramallah",
            "al-bireh", "al bireh", "west bank", "gaza", "nablus",
        ),
    ),
    # "AZ" is Arizona: Baku takes the row from it, and Azerbaijan has no code of its own.
    "AZ": Country("Azerbaijan", words=("azerbaijan", "baku")),
}
# fmt: on

#: India's names as they guard every other country: the whole-country word, every city alias,
#: every state, and ISO alpha-3 "IND". Not a clause of its own: ``country=IN`` is
#: :func:`headstart.search_filters.india_filter.clause` (see the module docstring).
INDIA = Country(
    "India",
    words=(
        "india",
        *india_gazetteer.CITIES,
        *(a for aliases in india_gazetteer.CITIES.values() for a in aliases),
        *india_gazetteer.STATES,
    ),
    segments=("ind",),
)

#: What may stand before and after a segment. Commas, semicolons, pipes, slashes, brackets and
#: colons always separate. A hyphen separates when a space is beside it ("Remote - US",
#: "SG - Singapore") or when the segment starts or ends the whole string ("US-Remote",
#: "US-CA-Santa Clara", "Remote-US"). A hyphen inside a place name does not, so the "la" of
#: "Louvain-la-Neuve" is never Louisiana. Whitespace alone never separates, so the "de" of
#: "Rio de Janeiro" is never Germany.
_LEFT = r"(?:^|[,;|/():]|\s[-–])\s*"
_RIGHT = r"(?:\s*(?:$|[,;|/():]|[-–]\s)|\s+or\s+remote(?:[^a-z0-9]|$))"
_HYPHEN_ENDS = (r"^\s*(?:{0})\s*[-–]", r"[-–]\s*(?:{0})\s*$")


def _escaped(term: str) -> str:
    """One term as a regex fragment safe inside a SQL string literal: the India gazetteer's
    ``_rx`` rule, ``re.escape`` for the regex and quote doubling for the literal."""
    return re.escape(term).replace("'", "''")


def _alternation(terms: Iterable[str]) -> str:
    return "|".join(_escaped(t) for t in sorted(set(terms), key=lambda t: (-len(t), t)))


def _regex(words: Iterable[str], segments: Iterable[str]) -> str | None:
    """One regex matching any of ``words`` as a word or any of ``segments`` as a whole segment,
    or None when both are empty: an empty alternation would match every row."""
    words, segments = tuple(words), tuple(segments)
    parts = []
    if words:
        parts.append(f"(?:^|[^a-z0-9])(?:{_alternation(words)})(?:[^a-z0-9]|$)")
    if segments:
        codes = _alternation(segments)
        parts.append(f"{_LEFT}(?:{codes}){_RIGHT}")
        parts += [end.format(codes) for end in _HYPHEN_ENDS]
    return "|".join(parts) or None


@dataclass(frozen=True)
class _Rule:
    """One country's compiled regexes: what :func:`where` emits and :func:`matches` runs."""

    sure: str
    shared_segments: str | None
    shared_words: str | None
    city_words: str | None
    others_sure: str
    others_shared_segments: str
    #: Another country stated: its English name, or any of its codes (ADR-0322).
    others_stated: str


@cache
def _rule(code: str) -> _Rule:
    country = COUNTRIES[code]
    others = [c for k, c in COUNTRIES.items() if k != code] + [INDIA]
    sure = _regex(country.words, country.segments)
    others_sure = _regex(
        (w for c in others for w in c.words), (s for c in others for s in c.segments)
    )
    others_shared = _regex((), (s for c in others for s in c.shared_segments))
    others_stated = _regex(
        (c.name.lower() for c in others),
        (s for c in others for s in (*c.segments, *c.shared_segments)),
    )
    assert sure and others_sure and others_shared and others_stated, code
    return _Rule(
        sure=sure,
        shared_segments=_regex((), country.shared_segments),
        shared_words=_regex(country.shared_words, ()),
        city_words=_regex(country.city_words, ()),
        others_sure=others_sure,
        others_shared_segments=others_shared,
        others_stated=others_stated,
    )


_LOC = "lower(location)"


def _like(regex: str) -> str:
    return f"regexp_like({_LOC}, '{regex}')"


def where(code: str) -> str | None:
    """The where-fragment for one country (not India: see the module docstring), or None for a
    code this gazetteer does not know.

    ``sure OR (city word AND NOT another country stated) OR ((shared segment OR (shared word
    AND NOT another's shared segment)) AND NOT another's sure name)``: at most seven
    ``regexp_like`` passes over ``location``, one per alternation however many names it holds
    (ADR-0024's single-automaton rule), and five for a country with no city word.
    """
    if code not in COUNTRIES:
        return None
    rule = _rule(code)
    parts = [_like(rule.sure)]
    if rule.city_words:
        parts.append(f"({_like(rule.city_words)} AND NOT {_like(rule.others_stated)})")
    shared = []
    if rule.shared_segments:
        shared.append(_like(rule.shared_segments))
    if rule.shared_words:
        shared.append(
            f"({_like(rule.shared_words)} AND NOT {_like(rule.others_shared_segments)})"
        )
    if shared:
        parts.append(f"(({' OR '.join(shared)}) AND NOT {_like(rule.others_sure)})")
    return "(" + " OR ".join(parts) + ")"


@cache
def _compiled(regex: str) -> re.Pattern[str]:
    # The SQL literal doubled any quote; Python reads the regex itself.
    return re.compile(regex.replace("''", "'"))


def _search(regex: str | None, text: str) -> bool:
    return bool(regex) and _compiled(regex).search(text) is not None


def matches(code: str, location: str | None) -> bool:
    """Whether ``location`` is in ``code`` by exactly the rule :func:`where` compiles to SQL.

    India is :func:`headstart.search_filters.india_gazetteer.classify`, the rule ``country=IN``
    runs. The same regex strings drive both sides, so this function and the SQL can disagree
    only where Python's regex engine and DataFusion's do;
    ``test_where_agrees_with_matches_on_a_real_table`` runs the two against each other.
    """
    if not location:
        return False
    if code == "IN":
        return india_gazetteer.classify(location) == "IN"
    if code not in COUNTRIES:
        return False
    text = location.lower()
    rule = _rule(code)
    if _search(rule.sure, text):
        return True
    if _search(rule.city_words, text) and not _search(rule.others_stated, text):
        return True
    shared = _search(rule.shared_segments, text) or (
        _search(rule.shared_words, text)
        and not _search(rule.others_shared_segments, text)
    )
    return shared and not _search(rule.others_sure, text)


def classify(location: str | None) -> set[str]:
    """Every country ``location`` is in: a row naming three countries is in all three."""
    return {code for code in (*COUNTRIES, "IN") if matches(code, location)}
