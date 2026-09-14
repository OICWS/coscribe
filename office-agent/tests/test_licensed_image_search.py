from coscribe.tools._licensed_image_search import (
    AssetCandidate,
    ImageSearchRequest,
    build_attribution_text,
    build_query_progression,
    classify_license,
    compute_relevance,
    missing_required_terms,
    normalize_license_name,
    score_candidate,
)


def _candidate(**overrides: object) -> AssetCandidate:
    defaults: dict[str, object] = {
        "provider": "openverse",
        "title": "Mountain Landscape Sunset",
        "author": "Jane Doe",
        "source_page_url": "https://example.com/photo",
        "license_name": "CC0",
        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "license_tier": "no-attribution",
        "width": 1920,
        "height": 1080,
        "download_url": "https://example.com/photo.jpg",
    }
    defaults.update(overrides)
    return AssetCandidate(**defaults)  # type: ignore[arg-type]


# --- classify_license ---


def test_classify_license_recognizes_cc0_as_no_attribution() -> None:
    assert classify_license("CC0") == "no-attribution"


def test_classify_license_recognizes_public_domain_as_no_attribution() -> None:
    assert classify_license("Public domain") == "no-attribution"


def test_classify_license_recognizes_cc_by_as_attribution_required() -> None:
    assert classify_license("CC BY 4.0") == "attribution-required"


def test_classify_license_recognizes_cc_by_sa_as_attribution_required() -> None:
    assert classify_license("CC BY-SA 4.0") == "attribution-required"


def test_classify_license_rejects_non_commercial() -> None:
    assert classify_license("CC BY-NC 4.0") is None


def test_classify_license_rejects_no_derivatives() -> None:
    assert classify_license("CC BY-ND 4.0") is None


def test_classify_license_rejects_all_rights_reserved() -> None:
    assert classify_license("All rights reserved") is None


def test_classify_license_rejects_unknown_license() -> None:
    assert classify_license("Some Proprietary License") is None


def test_classify_license_rejects_empty_input() -> None:
    assert classify_license("") is None


def test_classify_license_recognizes_pexels_license_token() -> None:
    assert classify_license("Pexels license") == "no-attribution"


def test_classify_license_provider_alone_does_not_substitute_for_a_real_license_string() -> None:
    # `provider` names which search API returned the candidate (only ever
    # "openverse"/"wikimedia" at the real call sites) -- it is not a
    # license hint, so a bare provider name with no license text/URL is
    # correctly rejected rather than guessed at.
    assert classify_license("", provider="openverse") is None


# --- normalize_license_name ---


def test_normalize_license_name_canonicalizes_cc0_variants() -> None:
    assert normalize_license_name("cc0") == "CC0"
    assert normalize_license_name("CC 0") == "CC0"


def test_normalize_license_name_canonicalizes_public_domain_variants() -> None:
    assert normalize_license_name("public domain") == "Public Domain"
    assert normalize_license_name("PDM") == "Public Domain"


def test_normalize_license_name_canonicalizes_cc_by_pattern() -> None:
    assert normalize_license_name("cc by 4.0") == "CC BY 4.0"
    assert normalize_license_name("cc-by-sa-4.0") == "CC BY-SA 4.0"


def test_normalize_license_name_handles_bare_openverse_slugs_without_cc_prefix() -> None:
    # Openverse's raw `license` field is a bare slug with no "cc" prefix
    # ("by", "by-sa", ...) and reports the version in a separate field --
    # confirmed live against the real API, not assumed.
    assert normalize_license_name("by 3.0") == "CC BY 3.0"
    assert normalize_license_name("by-sa 4.0") == "CC BY-SA 4.0"


def test_normalize_license_name_strips_trailing_version_for_canon_lookup() -> None:
    # "cc0" + license_version "1.0" combine to "cc0 1.0" before reaching
    # here -- must still canonicalize, not fall through unnormalized.
    assert normalize_license_name("cc0 1.0") == "CC0"
    assert normalize_license_name("public domain 1.0") == "Public Domain"


def test_normalize_license_name_passes_through_unknown_names() -> None:
    assert normalize_license_name("Some Weird License") == "Some Weird License"


def test_normalize_license_name_empty_input_returns_empty() -> None:
    assert normalize_license_name("") == ""


# --- missing_required_terms ---


def test_missing_required_terms_empty_when_none_required() -> None:
    assert missing_required_terms(_candidate(), ()) == []


def test_missing_required_terms_finds_a_match_in_the_title() -> None:
    candidate = _candidate(title="Eiffel Tower at dusk")
    assert missing_required_terms(candidate, ("eiffel",)) == []


def test_missing_required_terms_reports_a_genuinely_missing_term() -> None:
    candidate = _candidate(title="A random photo")
    assert missing_required_terms(candidate, ("eiffel tower",)) == ["eiffel tower"]


def test_missing_required_terms_accepts_any_pipe_separated_alternative() -> None:
    candidate = _candidate(title="Liberation Monument at night")
    assert missing_required_terms(candidate, ("Jiefangbei|Liberation Monument",)) == []


def test_missing_required_terms_ands_multiple_entries() -> None:
    candidate = _candidate(title="Eiffel Tower, no other landmark here")
    result = missing_required_terms(candidate, ("eiffel", "louvre"))
    assert result == ["louvre"]


# --- compute_relevance / score_candidate ---


def test_compute_relevance_full_match() -> None:
    candidate = _candidate(title="mountain landscape sunset")
    assert compute_relevance(candidate, "mountain landscape sunset") == 1.0


def test_compute_relevance_partial_match() -> None:
    candidate = _candidate(title="mountain photo")
    assert 0.0 < compute_relevance(candidate, "mountain landscape sunset") < 1.0


def test_compute_relevance_no_ascii_tokens_is_neutral() -> None:
    candidate = _candidate(title="anything")
    assert compute_relevance(candidate, "  ") == 1.0


def test_score_candidate_rejects_missing_license_tier() -> None:
    candidate = _candidate(license_tier="")
    request = ImageSearchRequest(query="mountain")
    assert score_candidate(candidate, request) == float("-inf")


def test_score_candidate_rejects_attribution_required_without_author() -> None:
    candidate = _candidate(license_tier="attribution-required", author="")
    request = ImageSearchRequest(query="mountain landscape sunset")
    assert score_candidate(candidate, request) == float("-inf")


def test_score_candidate_rejects_irrelevant_candidate() -> None:
    candidate = _candidate(title="completely unrelated subject matter")
    request = ImageSearchRequest(query="mountain landscape sunset")
    assert score_candidate(candidate, request) == float("-inf")


def test_score_candidate_rejects_missing_required_term() -> None:
    candidate = _candidate(title="mountain landscape sunset")
    request = ImageSearchRequest(query="mountain landscape sunset", required_terms=("eiffel",))
    assert score_candidate(candidate, request) == float("-inf")


def test_score_candidate_prefers_matching_orientation() -> None:
    landscape = _candidate(title="mountain landscape sunset", width=1920, height=1080)
    portrait = _candidate(title="mountain landscape sunset", width=1080, height=1920)
    request = ImageSearchRequest(query="mountain landscape sunset", orientation="landscape")
    assert score_candidate(landscape, request) > score_candidate(portrait, request)


def test_score_candidate_penalizes_below_minimum_size() -> None:
    big = _candidate(title="mountain landscape sunset", width=1920, height=1080)
    small = _candidate(title="mountain landscape sunset", width=200, height=150)
    request = ImageSearchRequest(query="mountain landscape sunset", min_width=800, min_height=600)
    assert score_candidate(big, request) > score_candidate(small, request)


def test_score_candidate_penalizes_infrastructure_terms_unless_explicitly_asked() -> None:
    plain = _candidate(title="Paris landmark")
    station = _candidate(title="Paris metro station platform")
    request = ImageSearchRequest(query="Paris landmark")
    assert score_candidate(plain, request) > score_candidate(station, request)


def test_score_candidate_no_attribution_scores_higher_than_attribution_required() -> None:
    free = _candidate(title="mountain landscape sunset", license_tier="no-attribution")
    credited = _candidate(
        title="mountain landscape sunset", license_tier="attribution-required", author="Jane Doe"
    )
    request = ImageSearchRequest(query="mountain landscape sunset")
    assert score_candidate(free, request) > score_candidate(credited, request)


# --- build_attribution_text ---


def test_build_attribution_text_includes_title_author_provider_license() -> None:
    candidate = _candidate(
        title="Sunset Photo",
        author="Jane Doe",
        provider="wikimedia",
        license_name="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0",
    )
    text = build_attribution_text(candidate)
    assert '"Sunset Photo"' in text
    assert "by Jane Doe" in text
    assert "via Wikimedia Commons" in text
    assert "CC BY 4.0" in text
    assert "https://creativecommons.org/licenses/by/4.0" in text


def test_build_attribution_text_omits_empty_fields_gracefully() -> None:
    candidate = _candidate(title="", author="", license_name="", license_url="")
    text = build_attribution_text(candidate)
    assert "via Openverse" in text
    assert '""' not in text


# --- build_query_progression ---


def test_build_query_progression_starts_with_the_original_query() -> None:
    progression = build_query_progression("a modern professional office team meeting")
    assert progression[0] == "a modern professional office team meeting"


def test_build_query_progression_ends_shorter_than_it_starts() -> None:
    progression = build_query_progression("a modern professional office team meeting")
    assert len(progression[-1].split()) <= len(progression[0].split())


def test_build_query_progression_deduplicates() -> None:
    progression = build_query_progression("mountain")
    assert len(progression) == len(set(progression))
