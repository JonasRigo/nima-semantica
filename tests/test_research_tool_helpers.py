"""Regression checks for helpers shared by the maintained research tools."""
import pytest
from nima_semantica.research_tool_helpers import execution_artifact, exact_anchors
from nima_semantica.calculation_values import equivalent, path_value, _scalar_expression


def test_execution_output_rejects_duplicate_keys_nonfinite_and_nonobjects():
    for text in ['{"value": 1, "value": 2}', '{"value": 1e999}', '{"value": NaN}', '[1, 2]']:
        with pytest.raises(ValueError):
            execution_artifact(text)
    assert execution_artifact('{"value": {"x": 2}}') == {'value': {'x': 2}}


def test_anchors_cover_exact_source_at_boundary():
    source = 'a' * 20000 + 'final'
    anchors = exact_anchors({'source': source, 'empty': ''})
    assert [(a.start, a.end) for a in anchors] == [(0, 20000), (20000, 20005)]
    assert ''.join(a.quotation for a in anchors) == source


def test_calculation_values_preserve_nested_symbolic_comparison():
    assert equivalent({'x': ['a+a', 1]}, {'x': ['2*a', 1]})
    assert not equivalent('a+1', 'a+2')
    assert path_value({'items': [2, 3]}, 'items.1') == 3
    assert path_value(7, '$') == 7
    assert str(_scalar_expression('x+x')) == '2*x'
    with pytest.raises(ValueError):
        _scalar_expression('open("file")')
