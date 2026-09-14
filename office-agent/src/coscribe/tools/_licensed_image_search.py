"""License-classified web image search against two zero-API-key providers
(Openverse, Wikimedia Commons) -- the real capability gap `images.py`'s
own module docstring already named: `search_images` (DuckDuckGo) returns
whatever the index surfaces with no license check at all, "unlike a
curated stock-photo API... which was considered and explicitly not
chosen." This module is that curated path, added once one actually
existed to adapt from.

Adapted, not vendored, from `hugohe3/ppt-master`'s `image_sources/
provider_common.py`/`provider_openverse.py`/`provider_wikimedia.py`
(MIT, commit `6e3ce9c5a3b994a0e223a14a0f7eddf42fd0b9f5`) -- credited here
rather than via a formal vendored-package NOTICE.md, the same ceremony
level `presentations.py`'s retyped `_TRANSITION_SPECS` already uses
(PPTX_DESIGN.md §27), not §25's "vendor whole files unmodified"
treatment: real adaptation was needed, not just retyping, since the
source uses `requests` and coscribe deliberately standardized on `httpx`
(see `tools/images.py`'s own `download_image`). What's kept: the license
classification token lists/tiers (real, hard-won knowledge -- which
license strings each provider actually returns, and which tier they map
to), the two providers' real API endpoints/response shapes, and the
scoring heuristic. What's dropped: ppt-master's own much larger
surrounding system (review-pool thumbnails, `image_sources.json`
provenance manifest, promote/batch/manual-URL workflows, `required_terms`'
"visual-review relaxation" scoring variant) -- none of it applies to
coscribe's own "search returns candidates, the model picks one and calls
the existing download_image tool" shape, which needs none of that
machinery.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# License tier classification
# ---------------------------------------------------------------------------

LICENSE_TIER_NO_ATTRIBUTION = "no-attribution"
LICENSE_TIER_ATTRIBUTION_REQUIRED = "attribution-required"

_NO_ATTRIBUTION_TOKENS: tuple[str, ...] = (
    "cc0",
    "public domain",
    "publicdomain",
    "creativecommons.org/publicdomain/",
    "pexels license",
    "pixabay content license",
    "pixabay license",
)
_ATTRIBUTION_REQUIRED_TOKENS: tuple[str, ...] = (
    "cc by",
    "cc-by",
    "by-sa",
    "by sa",
    "creativecommons.org/licenses/by/",
    "creativecommons.org/licenses/by-sa/",
)
_REJECTED_TOKENS: tuple[str, ...] = (
    "by-nc",
    "by nc",
    "noncommercial",
    "non-commercial",
    "by-nd",
    "by nd",
    "no derivatives",
    "noderivatives",
    "all rights reserved",
)
_LICENSE_NAME_CANON: dict[str, str] = {
    "cc0": "CC0",
    "cc 0": "CC0",
    "public domain": "Public Domain",
    "publicdomain": "Public Domain",
    "pdm": "Public Domain",
}
_CC_PATTERN = re.compile(
    r"^\s*(?:cc[\s-]+)?(by(?:[\s-]+(?:sa|nc|nd))*)[\s-]*([0-9.]*)\s*$", re.IGNORECASE
)


def normalize_license_name(name: str) -> str:
    """Canonical display form for a license string (providers report the
    same license with different capitalization -- e.g. Openverse "cc0" vs
    Wikimedia "Public domain")."""
    if not name:
        return ""
    key = name.strip().lower()
    if not key:
        return ""
    if key in _LICENSE_NAME_CANON:
        return _LICENSE_NAME_CANON[key]
    # Openverse reports license/license_version as separate fields (e.g.
    # "cc0" + "1.0"); once combined, "cc0 1.0" no longer matches the exact
    # canon key -- strip a trailing version number before falling through.
    base_key = re.sub(r"\s+[0-9][0-9.]*$", "", key).strip()
    if base_key in _LICENSE_NAME_CANON:
        return _LICENSE_NAME_CANON[base_key]
    cc_match = _CC_PATTERN.match(key)
    if cc_match:
        suffix_raw, version = cc_match.group(1), cc_match.group(2)
        suffix = suffix_raw.replace(" ", "-").upper()
        return f"CC {suffix} {version}".strip()
    return name.strip()


def classify_license(license_name: str, license_url: str = "", provider: str = "") -> str | None:
    """Classify a license string into one of the two accepted tiers, or
    reject it (`None`) -- covers CC-BY-NC/CC-BY-ND/all-rights-reserved/
    unknown, none of which this module will ever return a candidate for."""
    text = " ".join(
        part.strip().lower() for part in (license_name or "", license_url or "") if part
    )
    provider_key = (provider or "").strip().lower()
    if not text and not provider_key:
        return None
    if any(token in text for token in _REJECTED_TOKENS):
        return None
    if any(token in text for token in _NO_ATTRIBUTION_TOKENS):
        return LICENSE_TIER_NO_ATTRIBUTION
    if any(token in text for token in _ATTRIBUTION_REQUIRED_TOKENS):
        return LICENSE_TIER_ATTRIBUTION_REQUIRED
    return None


# ---------------------------------------------------------------------------
# Request / candidate shapes
# ---------------------------------------------------------------------------


@dataclass
class ImageSearchRequest:
    query: str
    orientation: str = ""  # "landscape" / "portrait" / "square" / ""
    min_width: int = 0
    min_height: int = 0
    required_terms: tuple[str, ...] = ()


@dataclass
class AssetCandidate:
    provider: str
    title: str
    asset_id: str = ""
    source_page_url: str = ""
    license_name: str = ""
    license_url: str = ""
    license_tier: str = ""
    width: int = 0
    height: int = 0
    download_url: str = ""
    preview_url: str = ""
    author: str = ""
    raw: Any = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Query simplification -- web image APIs do keyword matching against
# metadata, not semantic search; a long descriptive query returns zero
# results, so progressively trim down to the most concrete nouns.
# ---------------------------------------------------------------------------

_NOISE_WORDS = frozenset(
    {
        "using",
        "with",
        "from",
        "that",
        "this",
        "have",
        "been",
        "will",
        "into",
        "more",
        "also",
        "very",
        "some",
        "than",
        "them",
        "other",
    }
)
_SOFT_NOISE_WORDS = frozenset(
    {
        "ai",
        "code",
        "software",
        "system",
        "digital",
        "platform",
        "solution",
        "application",
        "interface",
        "framework",
        "algorithm",
        "api",
        "sdk",
        "assistant",
        "tool",
        "service",
        "technology",
        "tech",
        "program",
        "professional",
        "editorial",
        "commercial",
        "premium",
        "stock",
        "photo",
        "photograph",
        "photography",
        "image",
        "picture",
        "visual",
        "background",
        "hero",
        "cover",
        "banner",
        "wallpaper",
        "high",
        "quality",
        "resolution",
        "sharp",
        "clean",
        "cinematic",
        "dramatic",
        "lighting",
        "light",
        "modern",
        "natural",
        "visible",
    }
)
_TOKEN_STRIP_CHARS = ".,;:!?\"'()[]{}，。；：！？、"
_MATCH_SEPARATOR_RE = re.compile(r"""[\s\-_./:;,'"()[\]{}]+""")
_ASCII_MATCH_TOKEN_RE = re.compile(r"[a-z0-9]+")


def simplify_query(query: str, max_words: int = 4) -> str:
    cleaned = re.sub(r"#[0-9a-fA-F]{3,8}", "", query)
    cleaned = re.sub(r"\([^)]*\)", "", cleaned)
    words = [w.strip(_TOKEN_STRIP_CHARS) for w in cleaned.split()]
    words = [w for w in words if len(w) > 2]
    after_hard = [w for w in words if w.lower() not in _NOISE_WORDS]
    after_soft = [w for w in after_hard if w.lower() not in _SOFT_NOISE_WORDS]
    filtered = after_soft if after_soft else after_hard
    if not filtered:
        return query.strip()
    return " ".join(filtered[:max_words])


def build_query_progression(query: str) -> list[str]:
    """Progressively simpler queries to try in order, stopping at the
    first that yields results. Duplicates dropped, order preserved."""
    seen: set[str] = set()
    out: list[str] = []
    for candidate in (
        query,
        simplify_query(query, max_words=4),
        simplify_query(query, max_words=3),
        simplify_query(query, max_words=2),
        simplify_query(query, max_words=1),
    ):
        candidate = candidate.strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    return out


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _normalize_orientation(width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        return "unknown"
    if width > height:
        return "landscape"
    if height > width:
        return "portrait"
    return "square"


def _query_tokens(query: str) -> list[str]:
    cleaned = re.sub(r"#[0-9a-fA-F]{3,8}", "", query.lower())
    cleaned = re.sub(r"\([^)]*\)", "", cleaned)
    words = [w.strip(_TOKEN_STRIP_CHARS) for w in cleaned.split()]
    words = [w for w in words if len(w) > 2 and w.isascii()]
    if not words:
        return []
    after_hard = [w for w in words if w not in _NOISE_WORDS]
    after_soft = [w for w in after_hard if w not in _SOFT_NOISE_WORDS]
    return after_soft if after_soft else after_hard


def _candidate_text(candidate: AssetCandidate) -> str:
    return " ".join(
        filter(None, (candidate.title, candidate.author, candidate.source_page_url))
    ).lower()


def _candidate_match_tokens(candidate: AssetCandidate) -> set[str]:
    return set(_ASCII_MATCH_TOKEN_RE.findall(_candidate_text(candidate)))


def _normalize_match_text(text: str) -> str:
    return _MATCH_SEPARATOR_RE.sub(" ", (text or "").lower()).strip()


def _term_group_alternatives(term_group: str) -> list[str]:
    return [
        _normalize_match_text(part) for part in str(term_group or "").split("|") if part.strip()
    ]


def missing_required_terms(
    candidate: AssetCandidate, required_terms: tuple[str, ...] | None
) -> list[str]:
    """Entity-safety gate, not a fuzzy visual classifier -- for exact
    subjects (a company name, a named landmark) where a visually nice but
    wrong image is worse than none. Each entry may list `|`-separated
    alternatives; different entries are ANDed."""
    if not required_terms:
        return []
    text = _normalize_match_text(_candidate_text(candidate))
    compact_text = text.replace(" ", "")
    missing: list[str] = []
    for group in required_terms:
        alternatives = _term_group_alternatives(group)
        if not alternatives:
            continue
        matched = any(alt in text or alt.replace(" ", "") in compact_text for alt in alternatives)
        if not matched:
            missing.append(str(group))
    return missing


def compute_relevance(candidate: AssetCandidate, query: str) -> float:
    """Fraction of query tokens matching whole candidate-metadata tokens,
    [0.0, 1.0]. 1.0 (neutral) when the query has no ASCII tokens (a non-
    English query falls through to license/size scoring instead of being
    unfairly rejected)."""
    tokens = _query_tokens(query)
    if not tokens:
        return 1.0
    candidate_tokens = _candidate_match_tokens(candidate)
    if not candidate_tokens:
        return 0.0
    hits = sum(1 for token in tokens if token in candidate_tokens)
    return hits / len(tokens)


def score_candidate(candidate: AssetCandidate, request: ImageSearchRequest) -> float:
    """Higher is better; -inf rejects outright. Relevance dominates -- a
    candidate sharing no query tokens with its own metadata is rejected,
    so size/license/orientation can never rescue an irrelevant image."""
    if not candidate.license_tier:
        return float("-inf")
    if candidate.license_tier == LICENSE_TIER_ATTRIBUTION_REQUIRED and not candidate.author.strip():
        return float("-inf")
    if missing_required_terms(candidate, request.required_terms):
        return float("-inf")

    relevance = compute_relevance(candidate, request.query)
    if relevance == 0.0 and not request.required_terms:
        return float("-inf")

    score = relevance * 10000.0
    title_text = _normalize_match_text(candidate.title)
    compact_title = title_text.replace(" ", "")
    for group in request.required_terms or ():
        alternatives = _term_group_alternatives(group)
        if any(alt in title_text or alt.replace(" ", "") in compact_title for alt in alternatives):
            score += 1500.0

    # Penalize infrastructure/transit metadata unless explicitly asked for --
    # otherwise a high-res subway-station photo can outrank an actual
    # tourist landmark for the same query.
    text = _candidate_text(candidate)
    query_lower = request.query.lower()
    infra_terms = ("station", "subway", "metro", "rail", "transit", "airport", "bus")
    if not any(t in query_lower for t in infra_terms) and any(t in text for t in infra_terms):
        score -= 5000.0

    candidate_orientation = _normalize_orientation(candidate.width, candidate.height)
    requested = (request.orientation or "").strip().lower()
    if requested:
        score += 1000.0 if candidate_orientation == requested else -250.0

    if request.min_width and candidate.width < request.min_width:
        score -= 500.0
    if request.min_height and candidate.height < request.min_height:
        score -= 500.0
    if candidate.license_tier == LICENSE_TIER_NO_ATTRIBUTION:
        score += 250.0

    # Larger images score higher, but only as a tie-breaker.
    pixel_score = max(candidate.width, 0) * max(candidate.height, 0) / 1000.0
    score += min(pixel_score, 1500.0)
    return score


def build_attribution_text(candidate: AssetCandidate) -> str:
    """`"title" by author, via Provider, license: name (url)` -- the
    on-slide credit text for an `attribution-required` candidate. Empty
    fields are gracefully omitted."""
    provider_display = {"openverse": "Openverse", "wikimedia": "Wikimedia Commons"}.get(
        candidate.provider, candidate.provider or "unknown"
    )
    middle: list[str] = []
    if candidate.title:
        middle.append(f'"{candidate.title}"')
    if candidate.author:
        middle.append(f"by {candidate.author}")
    middle.append(f"via {provider_display}")
    parts = [" ".join(middle)]
    license_part = candidate.license_name or candidate.license_url
    if license_part:
        if candidate.license_url and candidate.license_name:
            license_part = f"{candidate.license_name} ({candidate.license_url})"
        parts.append(f"license: {license_part}")
    return " — ".join(parts)


# ---------------------------------------------------------------------------
# Providers -- httpx, not requests (coscribe's own client of choice, see
# tools/images.py's download_image), otherwise the same real endpoints/
# response-shape knowledge as the source.
# ---------------------------------------------------------------------------

_USER_AGENT = "coscribe/1.0 (+https://github.com/OICWS/project) licensed-image-search"

_OPENVERSE_API_URL = "https://api.openverse.org/v1/images/"
_OPENVERSE_ASPECT_MAP = {"landscape": "wide", "portrait": "tall", "square": "square"}

_WIKIMEDIA_API_URL = "https://commons.wikimedia.org/w/api.php"
_WIKIMEDIA_ACCEPTED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif"})
_WIKIMEDIA_TAG_RE = re.compile(r"<[^>]+>")
_WIKIMEDIA_WS_RE = re.compile(r"\s+")


def _parse_openverse_results(payload: dict[str, Any]) -> list[AssetCandidate]:
    candidates = []
    for item in payload.get("results", []) or []:
        license_name = (item.get("license") or "").strip()
        license_version = (item.get("license_version") or "").strip()
        license_url = (item.get("license_url") or "").strip()
        tier = classify_license(license_name, license_url, provider="openverse")
        if not tier:
            continue
        download_url = (item.get("url") or item.get("thumbnail") or "").strip()
        if not download_url:
            continue
        candidates.append(
            AssetCandidate(
                provider="openverse",
                title=(item.get("title") or "").strip() or "Untitled",
                asset_id=str(item.get("id") or ""),
                source_page_url=(
                    item.get("foreign_landing_url") or item.get("detail_url") or ""
                ).strip(),
                license_name=normalize_license_name(f"{license_name} {license_version}".strip()),
                license_url=license_url,
                license_tier=tier,
                width=int(item.get("width") or 0),
                height=int(item.get("height") or 0),
                download_url=download_url,
                preview_url=(item.get("thumbnail") or "").strip(),
                author=(item.get("creator") or "").strip(),
                raw=item,
            )
        )
    return candidates


def search_openverse(
    client: Any, request: ImageSearchRequest, *, page_size: int = 20, timeout: float = 20.0
) -> list[AssetCandidate]:
    """Search Openverse (zero-config, no API key) -- aggregates openly
    licensed images across Wikimedia/Flickr/museums/etc. `client` is a
    real `httpx.Client`. Returns candidates from the first query in the
    simplification progression that yields any."""
    orientation = (request.orientation or "").strip().lower()
    for query in build_query_progression(request.query):
        params: dict[str, str | int] = {
            "q": query,
            "page_size": page_size,
            "license": "by,by-sa,cc0,pdm",
            "size": "large",
        }
        if orientation in _OPENVERSE_ASPECT_MAP:
            params["aspect_ratio"] = _OPENVERSE_ASPECT_MAP[orientation]
        response = client.get(
            _OPENVERSE_API_URL,
            params=params,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        candidates = _parse_openverse_results(response.json())
        if candidates:
            return candidates
    return []


def _strip_html(value: str) -> str:
    if not value:
        return ""
    import html

    text = html.unescape(str(value))
    text = _WIKIMEDIA_TAG_RE.sub(" ", text)
    return _WIKIMEDIA_WS_RE.sub(" ", text).strip()


def _ext_value(extmetadata: dict[str, Any], key: str) -> str:
    entry = extmetadata.get(key) or {}
    if isinstance(entry, dict):
        return _strip_html(entry.get("value", ""))
    return _strip_html(entry)


def _parse_wikimedia_results(payload: dict[str, Any]) -> list[AssetCandidate]:
    candidates = []
    pages = (payload.get("query") or {}).get("pages") or {}
    for page in pages.values():
        title = page.get("title") or ""
        if not any(title.lower().endswith(ext) for ext in _WIKIMEDIA_ACCEPTED_EXTENSIONS):
            continue
        info_list = page.get("imageinfo") or []
        if not info_list:
            continue
        info = info_list[0]
        extmetadata = info.get("extmetadata") or {}
        license_name = _ext_value(extmetadata, "LicenseShortName") or _ext_value(
            extmetadata, "License"
        )
        license_url = _ext_value(extmetadata, "LicenseUrl")
        tier = classify_license(license_name, license_url, provider="wikimedia")
        if not tier:
            continue
        download_url = (info.get("url") or "").strip()
        if not download_url:
            continue
        page_label = _strip_html(title)
        if page_label.lower().startswith("file:"):
            page_label = page_label.split(":", 1)[1].strip()
        candidates.append(
            AssetCandidate(
                provider="wikimedia",
                title=page_label or "Untitled",
                asset_id=str(page.get("pageid") or ""),
                source_page_url=(info.get("descriptionurl") or "").strip(),
                license_name=normalize_license_name(license_name),
                license_url=license_url,
                license_tier=tier,
                width=int(info.get("width") or 0),
                height=int(info.get("height") or 0),
                download_url=download_url,
                preview_url=(info.get("thumburl") or "").strip(),
                author=_ext_value(extmetadata, "Artist"),
                raw=page,
            )
        )
    return candidates


def search_wikimedia(
    client: Any, request: ImageSearchRequest, *, search_limit: int = 20, timeout: float = 20.0
) -> list[AssetCandidate]:
    """Search Wikimedia Commons (zero-config, no API key) -- strong on
    educational/scientific/geographic/historical imagery, weaker on
    contemporary stock-style photography. `client` is a real
    `httpx.Client`."""
    orientation = (request.orientation or "").strip().lower()
    for query in build_query_progression(request.query):
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrnamespace": "6",  # File: namespace
            "gsrsearch": f"{query} filetype:bitmap",
            "gsrlimit": search_limit,
            "prop": "imageinfo",
            "iiprop": "url|size|extmetadata|mime",
            "iiurlwidth": 1024,
            "iiextmetadatafilter": "LicenseShortName|License|LicenseUrl|Artist",
        }
        response = client.get(
            _WIKIMEDIA_API_URL,
            params=params,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        candidates = _parse_wikimedia_results(response.json())
        if orientation and orientation != "any":
            matching = [
                c for c in candidates if _normalize_orientation(c.width, c.height) == orientation
            ]
            candidates = matching or candidates  # off-orientation beats no image at all
        if candidates:
            return candidates
    return []
