from typing import Optional

from coscribe.workflows.catalog import arg_descriptions, describe_params


def sample(
    path: str,
    rows: list[str],
    count: Optional[int] = None,  # noqa: UP045
    ratio: float = 0.5,
    strict: bool = False,
    note: str = "hi",
) -> None:
    """Do a thing.

    Args:
        path: file to read, relative to the
            workspace root
        rows: rows to write
        count (int): how many
        ratio: a fraction
        strict: fail on the first problem

    Returns:
        nothing: really
    """


def test_describe_params_reads_types_defaults_and_meanings() -> None:
    params = {p["name"]: p for p in describe_params(sample)}

    assert params["path"] == {
        "name": "path",
        "type": "text",
        "required": True,
        "default": None,
        "description": "file to read, relative to the workspace root",
    }
    assert params["rows"]["type"] == "other"
    assert params["count"]["type"] == "number" and params["count"]["required"] is False
    assert params["ratio"]["default"] == 0.5
    assert params["strict"]["type"] == "boolean"
    assert params["note"]["description"] == ""


def test_arg_descriptions_stops_at_the_next_section() -> None:
    assert "nothing" not in arg_descriptions(sample.__doc__ or "")
