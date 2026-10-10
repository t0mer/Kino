import pytest

from kino.slug import slugify


def test_hebrew_transliteration() -> None:
    assert slugify("נועה") == "noa"


def test_ascii_lowercase_and_separators() -> None:
    assert slugify("Class 3B / Parents!") == "class-3b-parents"


@pytest.mark.parametrize(
    "text", ["../../etc", "/abs/path", "a\x00b", "  ", "", "...", "😀", "a b\tc"]
)
def test_always_safe_nonempty(text: str) -> None:
    s = slugify(text)
    assert s and len(s) <= 40
    assert not any(c in s for c in "/\\\x00 \t") and ".." not in s
    assert s == s.lower()


def test_fallback_stable_and_distinct() -> None:
    assert slugify("😀") == slugify("😀")
    assert slugify("😀") != slugify("😁")


def test_long_truncated() -> None:
    assert len(slugify("a" * 200)) <= 40
