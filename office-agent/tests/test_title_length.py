"""The title the model gives a conversation fits a sidebar row."""

from coscribe.conversation.session import _clean_title


def test_a_short_title_is_kept_as_it_is() -> None:
    assert _clean_title('"Q3 sales summary"') == "Q3 sales summary"
    assert _clean_title("Q3 销售汇总。") == "Q3 销售汇总"


def test_a_sentence_is_cut_at_a_word_with_an_ellipsis() -> None:
    title = _clean_title(
        "Reading the attached contract for termination clauses, notice periods and penalties"
    )

    assert title.endswith("…")
    assert len(title) <= 41
    assert " " not in title[-3:]  # not in the middle of a word it could have kept whole


def test_chinese_counts_double_and_only_the_first_line_is_used() -> None:
    first_line = "请帮我把第三季度的销售数据汇总成一张表格并且按地区分组然后做成图表发给我"
    title = _clean_title(f"{first_line}\nsecond line")

    assert title.endswith("…")
    assert len(title) <= 21
    assert "second" not in title
