from dataclasses import dataclass

from codeguard.token_budget import fit_to_budget


@dataclass
class _Item:
    content: str


def _content_of(item: _Item) -> str:
    return item.content


def _replace_content(item: _Item, new_content: str) -> _Item:
    return _Item(content=new_content)


def test_fit_to_budget_keeps_everything_under_budget():
    items = [_Item("a" * 40), _Item("b" * 40)]
    result = fit_to_budget(items, max_tokens=100, content_of=_content_of, replace_content=_replace_content)
    assert result == items


def test_fit_to_budget_truncates_first_item_that_overflows():
    items = [_Item("a" * 400)]
    result = fit_to_budget(items, max_tokens=30, content_of=_content_of, replace_content=_replace_content)
    assert len(result) == 1
    assert "truncated" in result[0].content


def test_fit_to_budget_drops_everything_after_the_cutoff():
    items = [_Item("a" * 40), _Item("b" * 400), _Item("c" * 40)]
    result = fit_to_budget(items, max_tokens=40, content_of=_content_of, replace_content=_replace_content)
    assert len(result) == 2  # first item kept whole, second truncated, third dropped
    assert result[0].content == "a" * 40
    assert "truncated" in result[1].content


def test_fit_to_budget_drops_item_entirely_when_no_room_even_for_the_marker():
    # A budget smaller than the truncation marker itself can't produce a
    # truncated-but-marked item without exceeding the budget - dropping
    # it is the only option that actually respects max_tokens.
    items = [_Item("a" * 400)]
    result = fit_to_budget(items, max_tokens=1, content_of=_content_of, replace_content=_replace_content)
    assert result == []


def test_fit_to_budget_zero_budget_returns_nothing():
    items = [_Item("a" * 40)]
    assert fit_to_budget(items, max_tokens=0, content_of=_content_of, replace_content=_replace_content) == []


def test_fit_to_budget_preserves_order_of_kept_items():
    items = [_Item("a" * 8), _Item("b" * 8), _Item("c" * 8)]
    result = fit_to_budget(items, max_tokens=100, content_of=_content_of, replace_content=_replace_content)
    assert [i.content[0] for i in result] == ["a", "b", "c"]