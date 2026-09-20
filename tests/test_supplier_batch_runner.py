import argparse

import pytest

from supplier_batch_runner import _build_source_from_args


def _args(**overrides):
    defaults = dict(from_brand_batch=None, status="PURSUE", include="", exclude="", brands=None, input=None)
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_requires_exactly_one_source():
    with pytest.raises(SystemExit):
        _build_source_from_args(_args())
    with pytest.raises(SystemExit):
        _build_source_from_args(_args(brands="A,B", input="x.csv"))


def test_brands_mode_dedupes_and_is_standalone():
    mode, source = _build_source_from_args(_args(brands="Lemax, Lemax, Diamine"))
    assert mode == "standalone"
    assert source["brands"] == ["Lemax", "Diamine"]


def test_input_mode_is_standalone():
    mode, source = _build_source_from_args(_args(input="brands.csv"))
    assert mode == "standalone"
    assert source["input"] == "brands.csv"


def test_from_brand_batch_mode_is_integrated_with_include_exclude():
    mode, source = _build_source_from_args(_args(from_brand_batch="summary_x", include="A,B", exclude="C"))
    assert mode == "integrated"
    assert source["from_brand_batch"] == "summary_x"
    assert source["status"] == "PURSUE"
    assert source["include"] == ["A", "B"]
    assert source["exclude"] == ["C"]
