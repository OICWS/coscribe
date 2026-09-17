from coscribe.tools._output_truncation import MAX_SCRIPT_OUTPUT_CHARS, truncate_script_output


def test_truncate_script_output_is_a_no_op_under_the_cap() -> None:
    text = "hello\n" * 100
    assert len(text) <= MAX_SCRIPT_OUTPUT_CHARS
    assert truncate_script_output(text) == text


def test_truncate_script_output_is_a_no_op_exactly_at_the_cap() -> None:
    text = "x" * MAX_SCRIPT_OUTPUT_CHARS
    assert truncate_script_output(text) == text


def test_truncate_script_output_keeps_both_ends_past_the_cap() -> None:
    head = "HEAD_MARKER" + "a" * 50_000
    tail = "b" * 50_000 + "TAIL_MARKER"
    text = head + tail

    result = truncate_script_output(text)

    assert len(result) < len(text)
    assert result.startswith("HEAD_MARKER")
    assert result.endswith("TAIL_MARKER")
    assert "characters truncated" in result


def test_truncate_script_output_reports_the_real_omitted_count() -> None:
    text = "x" * (MAX_SCRIPT_OUTPUT_CHARS + 1_234)

    result = truncate_script_output(text)

    # 4000 head + 4000 tail kept -- everything else in between is the
    # reported omitted count.
    expected_omitted = len(text) - 4_000 - 4_000
    assert f"{expected_omitted:,} characters truncated" in result
