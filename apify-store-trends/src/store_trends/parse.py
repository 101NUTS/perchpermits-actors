"""Pure transforms over Apify Store items: flatten, price label, target bucket,
gap signals. JSON in, dicts out, no network.

The target list and the hostility labels are the same ones the developer's own
store scan used to pick niches. Field names are stable API surface; add, do
not rename.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any


class ParseDrift(Exception):
    """The store payload no longer looks like what this code was written for."""


# The store's category enum as of 2026-09-12 (FINANCE, HEALTH, ENTERTAINMENT and
# LIFESTYLE were dropped by the API between 2026-09-09 and 2026-09-12). A slice
# the API rejects is skipped with a log line, not fatal: see service.sweep_items.
CATS = ["AUTOMATION", "LEAD_GENERATION", "DEVELOPER_TOOLS", "SOCIAL_MEDIA", "ECOMMERCE",
        "OTHER", "AI", "JOBS", "REAL_ESTATE", "INTEGRATIONS", "BUSINESS", "SEO_TOOLS",
        "AGENTS", "VIDEOS", "NEWS", "TRAVEL", "MCP_SERVERS", "MARKETING", "OPEN_SOURCE",
        "SPORTS", "FOR_CREATORS", "EDUCATION", "GAMES", "DEVELOPER_EXAMPLES", "COVID_19"]
PRICING = ["FREE", "PAY_PER_EVENT", "PRICE_PER_DATASET_ITEM", "FLAT_PRICE_PER_MONTH"]

# Target sites, matched against title + name only (descriptions drag generic
# actors into every bucket they mention).
# Order matters: first match wins, so put specific names before generic ones.
TARGETS = [
    ("google maps", r"google ?maps|google places|gmaps|google business"),
    ("google search", r"google search|google serp|serp api|google results"),
    ("google shopping", r"google shopping"),
    ("google play", r"google play|play store"),
    ("google news", r"google news"),
    ("google trends", r"google trends"),
    ("google reviews", r"google reviews?"),
    ("google jobs", r"google jobs"),
    ("google flights", r"google flights"),
    ("google other", r"google (?:sheets|drive|scholar|images|lens|ads|analytics|patents|hotels|finance|books)"),
    ("youtube", r"youtube|yt "),
    ("instagram", r"instagram|\big\b"),
    ("tiktok", r"tiktok|tik tok"),
    ("facebook", r"facebook|\bfb\b|meta ads"),
    ("threads", r"\bthreads\b"),
    ("x/twitter", r"twitter|\bx\.com|\btweets?\b|(?<![a-z])x (?:scraper|posts|profile)"),
    ("linkedin", r"linkedin"),
    ("reddit", r"reddit"),
    ("pinterest", r"pinterest"),
    ("snapchat", r"snapchat"),
    ("bluesky", r"bluesky|bsky"),
    ("telegram", r"telegram"),
    ("discord", r"discord"),
    ("whatsapp", r"whatsapp"),
    ("twitch", r"twitch"),
    ("kick", r"\bkick\.com|kick stream"),
    ("onlyfans", r"onlyfans"),
    ("quora", r"quora"),
    ("medium", r"medium\.com|medium articles"),
    ("substack", r"substack"),
    ("tumblr", r"tumblr"),
    ("vk", r"\bvk\b|vkontakte"),
    ("weibo", r"weibo"),
    ("xiaohongshu", r"xiaohongshu|rednote|little red book"),
    ("douyin", r"douyin"),
    ("bilibili", r"bilibili"),
    ("amazon", r"amazon"),
    ("ebay", r"\bebay"),
    ("walmart", r"walmart"),
    ("etsy", r"\betsy"),
    ("shopify", r"shopify"),
    ("aliexpress", r"aliexpress"),
    ("alibaba", r"alibaba|1688"),
    ("temu", r"\btemu"),
    ("shein", r"\bshein"),
    ("taobao/tmall", r"taobao|tmall"),
    ("target", r"target\.com|target store|target product"),
    ("best buy", r"best ?buy"),
    ("home depot", r"home ?depot"),
    ("lowes", r"lowe'?s"),
    ("costco", r"costco"),
    ("wayfair", r"wayfair"),
    ("ikea", r"\bikea"),
    ("zalando", r"zalando"),
    ("otto", r"\botto\.de"),
    ("mercado libre", r"mercado ?libre|mercadolivre"),
    ("flipkart", r"flipkart"),
    ("myntra", r"myntra"),
    ("noon", r"\bnoon\.com"),
    ("lazada", r"lazada"),
    ("shopee", r"shopee"),
    ("tokopedia", r"tokopedia"),
    ("rakuten", r"rakuten"),
    ("coupang", r"coupang"),
    ("allegro", r"allegro"),
    ("bol.com", r"\bbol\.com"),
    ("idealo", r"idealo"),
    ("cdiscount", r"cdiscount"),
    ("leboncoin", r"leboncoin"),
    ("vinted", r"vinted"),
    ("depop", r"depop"),
    ("poshmark", r"poshmark"),
    ("mercari", r"mercari"),
    ("stockx", r"stockx"),
    ("goat", r"\bgoat\b"),
    ("grailed", r"grailed"),
    ("craigslist", r"craigslist"),
    ("facebook marketplace", r"marketplace"),
    ("offerup", r"offerup"),
    ("gumtree", r"gumtree"),
    ("olx", r"\bolx\b"),
    ("kleinanzeigen", r"kleinanzeigen"),
    ("ebay kleinanzeigen", r"ebay-kleinanzeigen"),
    ("zillow", r"zillow"),
    ("redfin", r"redfin"),
    ("realtor.com", r"realtor\.com|realtor scraper"),
    ("trulia", r"trulia"),
    ("apartments.com", r"apartments\.com"),
    ("rightmove", r"rightmove"),
    ("zoopla", r"zoopla"),
    ("idealista", r"idealista"),
    ("immobilienscout", r"immobilienscout|immoscout"),
    ("immoweb", r"immoweb"),
    ("seloger", r"seloger"),
    ("funda", r"\bfunda"),
    ("domain.com.au", r"domain\.com\.au"),
    ("realestate.com.au", r"realestate\.com\.au"),
    ("loopnet", r"loopnet"),
    ("crexi", r"crexi"),
    ("homes.com", r"homes\.com"),
    ("propertyguru", r"propertyguru"),
    ("magicbricks", r"magicbricks|99acres|housing\.com"),
    ("indeed", r"indeed"),
    ("glassdoor", r"glassdoor"),
    ("ziprecruiter", r"ziprecruiter"),
    ("monster", r"monster\.com|monster jobs"),
    ("dice", r"\bdice\b"),
    ("upwork", r"upwork"),
    ("fiverr", r"fiverr"),
    ("freelancer", r"freelancer\.com"),
    ("naukri", r"naukri"),
    ("seek", r"\bseek\b"),
    ("stepstone", r"stepstone"),
    ("welcome to the jungle", r"welcome to the jungle|wttj"),
    ("greenhouse/lever", r"greenhouse|lever\.co|ashby|workable|workday|smartrecruiters"),
    ("wellfound", r"wellfound|angellist"),
    ("crunchbase", r"crunchbase"),
    ("pitchbook", r"pitchbook"),
    ("apollo", r"apollo\.io|apollo"),
    ("zoominfo", r"zoominfo"),
    ("lusha", r"lusha"),
    ("rocketreach", r"rocketreach"),
    ("hunter.io", r"hunter\.io"),
    ("clutch", r"\bclutch"),
    ("g2", r"\bg2\b|g2\.com"),
    ("capterra", r"capterra"),
    ("trustpilot", r"trustpilot"),
    ("yelp", r"\byelp"),
    ("tripadvisor", r"tripadvisor"),
    ("booking.com", r"booking\.com|booking hotel|booking scraper"),
    ("airbnb", r"airbnb"),
    ("vrbo", r"\bvrbo"),
    ("expedia", r"expedia"),
    ("hotels.com", r"hotels\.com"),
    ("agoda", r"agoda"),
    ("trivago", r"trivago"),
    ("kayak", r"kayak"),
    ("skyscanner", r"skyscanner"),
    ("flightradar", r"flightradar|flight ?aware|adsb|ads-b|opensky"),
    ("marinetraffic", r"marinetraffic|vesselfinder|\bais\b"),
    ("uber eats", r"uber ?eats"),
    ("doordash", r"doordash"),
    ("grubhub", r"grubhub"),
    ("deliveroo", r"deliveroo"),
    ("just eat", r"just ?eat|lieferando|takeaway\.com"),
    ("zomato", r"zomato"),
    ("swiggy", r"swiggy"),
    ("opentable", r"opentable|resy"),
    ("yellow pages", r"yellow ?pages|yellowpages|pages ?jaunes|gelbe ?seiten|paginas ?amarillas"),
    ("bbb", r"\bbbb\b|better business bureau"),
    ("houzz", r"houzz"),
    ("angi/homeadvisor", r"\bangi\b|homeadvisor|thumbtack"),
    ("nextdoor", r"nextdoor"),
    ("imdb", r"\bimdb"),
    ("rotten tomatoes", r"rotten ?tomatoes|letterboxd"),
    ("spotify", r"spotify"),
    ("apple", r"app store|apple music|apple podcasts|itunes|apple\.com"),
    ("soundcloud", r"soundcloud"),
    ("steam", r"\bsteam"),
    ("epic games", r"epic games"),
    ("roblox", r"roblox"),
    ("github", r"github"),
    ("gitlab", r"gitlab"),
    ("npm/pypi", r"\bnpm\b|pypi|packagist|crates\.io"),
    ("stack overflow", r"stack ?overflow|stackexchange"),
    ("hacker news", r"hacker ?news|\bhn\b"),
    ("product hunt", r"product ?hunt"),
    ("hugging face", r"hugging ?face"),
    ("arxiv", r"arxiv|pubmed|semantic scholar|scholar"),
    ("wikipedia", r"wikipedia|wikidata"),
    ("news sites", r"\bnews\b|bbc|cnn|nytimes|reuters|bloomberg|guardian|fox news"),
    ("sec edgar", r"\bsec\b|edgar|10-k|13f"),
    ("yahoo finance", r"yahoo finance|yfinance"),
    ("tradingview", r"tradingview"),
    ("coinmarketcap", r"coinmarketcap|coingecko|dexscreener|dextools"),
    ("crypto/onchain", r"crypto|blockchain|ethereum|solana|bitcoin|on-?chain|defi|nft|bittensor|\btao\b|opensea|uniswap|etherscan"),
    ("polymarket", r"polymarket|kalshi"),
    ("stocks", r"stocks?\b|nasdaq|nyse|finviz|investing\.com|morningstar"),
    ("courts/legal", r"court|docket|pacer|case law|legal|lawsuit"),
    ("government/permits", r"permit|zoning|planning commission|assessor|county|municipal|\.gov\b|government|public records|foia|uspto|patent|trademark|tender|procurement"),
    ("sam.gov/grants", r"sam\.gov|grants\.gov|usaspending"),
    ("companies house", r"companies house|company registry|opencorporates|handelsregister|kvk|sirene|infogreffe"),
    ("dun & bradstreet", r"dun ?& ?bradstreet|d&b"),
    ("healthcare", r"doctor|physician|healthgrades|zocdoc|vitals|npi registry|clinicaltrials|drugs\.com|webmd"),
    ("tennis", r"tennis"),
    ("sports", r"espn|nba|nfl|mlb|nhl|fifa|premier league|sofascore|flashscore|betting|odds|draftkings|fanduel|transfermarkt|golf|cricket|\bufc\b|sports?\b|livescore|fotmob|whoscored|fbref|understat"),
    ("weather", r"weather|noaa|accuweather"),
    ("cars", r"autotrader|cars\.com|cargurus|carfax|mobile\.de|autoscout|copart|iaai|kbb|edmunds|vehicle|\bvin\b|car listings|carvana"),
    ("boats/rv", r"boat|yacht|\brv\b"),
    ("events/tickets", r"eventbrite|ticketmaster|meetup|lu\.ma|luma|stubhub|seatgeek|vivid seats|events?\b"),
    ("dating", r"tinder|bumble|hinge|dating"),
    ("education", r"coursera|udemy|edx|khan|university|college|scholar"),
    ("food/recipes", r"recipe|allrecipes|menu\b|restaurant"),
    ("email/contacts", r"email finder|email extractor|contact (?:details|info|finder|extractor)|phone number|emails?\b"),
    ("website crawler", r"website content|web ?crawler|crawl(?:er|ing)?\b|site ?map|sitemap|any website|universal|cheerio|puppeteer|playwright"),
    ("screenshot/pdf", r"screenshot|pdf|html to"),
    ("seo tools", r"\bseo\b|backlinks?|keywords?\b|ahrefs|semrush|similarweb|domain authority|moz\b|ubersuggest"),
    ("ai/llm", r"\bai\b|\bllm\b|gpt|openai|claude|gemini|chatbot|agent|rag\b|embedding|summar"),
    ("proxy/captcha", r"proxy|captcha|anti-?bot|cloudflare|bypass"),
    ("mcp", r"\bmcp\b"),
    ("skip trace/people search", r"skip ?trace|people (?:search|finder)|background check|truepeoplesearch|spokeo|whitepages|fastpeople"),
    ("phone validation", r"phone (?:valid|verif|lookup|number)|carrier lookup|tcpa"),
    ("ozon", r"\bozon"),
    ("avito", r"avito"),
    ("wildberries", r"wildberries"),
    ("2gis/yandex", r"2gis|yandex"),
    ("xing", r"\bxing"),
    ("india job boards", r"infojobs|internshala|unstop|foundit|hirist|instahyre|shine\.com"),
    ("immobiliare", r"immobiliare|subito"),
    ("truth social", r"truth social"),
    ("transcription", r"transcri|speech to text|audio to text"),
    ("image/video download", r"downloader|download (?:images|videos)|bulk image"),
    ("tech lookup", r"builtwith|wappalyzer|tech ?stack|technology (?:lookup|detect)"),
    ("http/fetch utility", r"web fetch|http request|send http|fetch url|url to"),
    ("remote/aggregate jobs", r"remote jobs|jobs? aggregat|all jobs|job board|career site|himalayas|remoteok|we work remotely"),
    ("y combinator", r"\byc\b|y combinator"),
    ("osint/username", r"osint|username (?:check|search)|maigret|sherlock"),
]
TARGET_RE = [(name, re.compile(rx)) for name, rx in TARGETS]
UNMATCHED = "(unmatched)"

# Sites that actively fight scrapers: expect frequent breakage and on-call work.
HOSTILE = {
    "high": {"instagram", "tiktok", "facebook", "facebook marketplace", "threads", "x/twitter",
             "linkedin", "amazon", "google search", "google maps", "google shopping",
             "zillow", "indeed", "glassdoor", "ticketmaster", "onlyfans", "apollo",
             "zoominfo", "craigslist", "temu", "shein", "walmart", "target", "booking.com",
             "airbnb", "expedia", "tripadvisor", "yelp", "etsy", "ebay", "youtube",
             "reddit", "pinterest", "snapchat", "upwork", "crunchbase", "stockx",
             "rightmove", "realtor.com", "redfin", "cars", "events/tickets", "sports", "dating"},
    "low": {"government/permits", "sec edgar", "sam.gov/grants", "companies house",
            "courts/legal", "arxiv", "wikipedia", "hacker news", "github", "gitlab",
            "npm/pypi", "stack overflow", "weather", "hugging face", "flightradar",
            "marinetraffic", "polymarket", "coinmarketcap", "crypto/onchain", "healthcare",
            "greenhouse/lever", "product hunt", "substack", "medium", "bluesky",
            "yahoo finance", "google trends", "google news"},
}


def hostility(target: str) -> str:
    return "high" if target in HOSTILE["high"] else "low" if target in HOSTILE["low"] else "med"


def target_for(title: str | None, name: str | None) -> str:
    """First target regex that matches title + name (hyphens and underscores read as spaces)."""
    text = " ".join([title or "", name or ""]).lower()
    text = text.replace("-", " ").replace("_", " ")
    for tname, rx in TARGET_RE:
        if rx.search(text):
            return tname
    return UNMATCHED


def query_match(query: str | None, title: str | None, name: str | None) -> bool | None:
    """Whether every word of `query` appears in the actor's title or name (hyphens and
    underscores read as spaces). The store's search is token-OR over descriptions too,
    so a query like `google trends` also returns every Google actor; this separates the
    actors that are about the query from the ones that merely came back. None without a query."""
    words = [w for w in (query or "").lower().split() if w]
    if not words:
        return None
    text = " ".join([title or "", name or ""]).lower().replace("-", " ").replace("_", " ")
    return all(w in text for w in words)


def price_label(row: dict[str, Any]) -> str:
    """Human price label per pricing model, e.g. `$0.0027/Result`, `$30/mo`, `$0.005/item`, `FREE`."""
    p = row.get("currentPricingInfo") or {}
    m = p.get("pricingModel")
    if m == "FLAT_PRICE_PER_MONTH":
        return f"${p.get('pricePerUnitUsd', '?')}/mo"
    if m == "PRICE_PER_DATASET_ITEM":
        return f"${p.get('pricePerUnitUsd', '?')}/item"
    if m == "PAY_PER_EVENT":
        ev = (p.get("pricingPerEvent") or {}).get("actorChargeEvents") or {}
        prim = next((e for e in ev.values() if e.get("isPrimaryEvent")), None) or next(iter(ev.values()), None)
        if prim:
            tiers = prim.get("eventTieredPricingUsd") or {}
            free = (tiers.get("FREE") or {}).get("tieredEventPriceUsd")
            if free is None:
                free = prim.get("eventPriceUsd")
            return f"${free}/{prim.get('eventTitle', 'event')[:24]}"
        return "PPE"
    return m or "?"


def primary_event_price(row: dict[str, Any]) -> float | None:
    """Numeric price of the primary pay-per-event event (FREE tier), or the per-item
    / per-month unit price for the other paid models. None for FREE and unknown."""
    p = row.get("currentPricingInfo") or {}
    m = p.get("pricingModel")
    if m in ("FLAT_PRICE_PER_MONTH", "PRICE_PER_DATASET_ITEM"):
        v = p.get("pricePerUnitUsd")
        return float(v) if isinstance(v, (int, float)) else None
    if m == "PAY_PER_EVENT":
        ev = (p.get("pricingPerEvent") or {}).get("actorChargeEvents") or {}
        prim = next((e for e in ev.values() if e.get("isPrimaryEvent")), None) or next(iter(ev.values()), None)
        if not prim:
            return None
        tiers = prim.get("eventTieredPricingUsd") or {}
        v = (tiers.get("FREE") or {}).get("tieredEventPriceUsd")
        if v is None:
            v = prim.get("eventPriceUsd")
        return float(v) if isinstance(v, (int, float)) else None
    return None


def flatten(row: dict[str, Any], now: datetime) -> dict[str, Any]:
    """One store item to one flat row. Raises ParseDrift when the item is not
    shaped like a store actor (no `stats.totalUsers`)."""
    if not isinstance(row, dict) or not row.get("name") or not row.get("username"):
        raise ParseDrift("store item has no username/name")
    s = row.get("stats")
    if not isinstance(s, dict) or "totalUsers" not in s:
        raise ParseDrift(f"store item {row.get('username')}/{row.get('name')} has no stats.totalUsers")
    runs = s.get("publicActorRunStats30Days") or {}
    total = runs.get("TOTAL") or 0
    bad = (runs.get("FAILED") or 0) + (runs.get("TIMED-OUT") or 0)
    last = s.get("lastRunStartedAt")
    days_idle = None
    if last:
        try:
            days_idle = (now - datetime.fromisoformat(last.replace("Z", "+00:00"))).days
        except ValueError:
            days_idle = None
    rating = s.get("actorReviewRating")
    return {
        "actor": f"{row['username']}/{row['name']}",
        "actor_id": row.get("id"),
        "title": (row.get("title") or "").strip(),
        "target": target_for(row.get("title"), row.get("name")),
        "categories": list(row.get("categories") or []),
        "users_total": s.get("totalUsers") or 0,
        "users_90d": s.get("totalUsers90Days") or 0,
        "users_30d": s.get("totalUsers30Days") or 0,
        "users_7d": s.get("totalUsers7Days") or 0,
        "runs_30d": total,
        "fail_rate_30d": round(bad / total, 3) if total else None,
        "rating": round(rating, 2) if rating else None,
        "reviews": s.get("actorReviewCount") or 0,
        "bookmarks": s.get("bookmarkCount") or 0,
        "price": price_label(row),
        "price_usd": primary_event_price(row),
        "pricing_model": (row.get("currentPricingInfo") or {}).get("pricingModel"),
        "notice": row.get("notice") or "",
        "days_since_last_run": days_idle,
        "agentic_payments": row.get("isWhiteListedForAgenticPayments"),
        "url": f"https://apify.com/{row['username']}/{row['name']}",
    }


def gap_signals(r: dict[str, Any]) -> list[str]:
    """Why this incumbent might be beatable. Empty list means nothing stands out."""
    out: list[str] = []
    fail = r.get("fail_rate_30d")
    users = r.get("users_30d") or 0
    reviews = r.get("reviews") or 0
    rating = r.get("rating")
    if fail is not None and fail >= 0.3 and users >= 10:
        out.append("leader_failing")
    if reviews < 3 and users >= 20:
        out.append("unrated")
    if r.get("notice") == "UNDER_MAINTENANCE":
        out.append("under_maintenance")
    if (r.get("days_since_last_run") or 0) >= 14:
        out.append("idle_14d")
    if rating is not None and reviews >= 3 and rating < 3.6:
        out.append("low_rated")
    return out
