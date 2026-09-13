import runpy
from pathlib import Path

from headstart.scrapers.registry import DISABLED_ATS, SCRAPERS


def test_every_enabled_scraper_has_a_live_filter_harness_url_shape():
    path = Path(__file__).resolve().parents[1] / "scripts/eval/verify_filters.py"
    shapes = runpy.run_path(str(path))["URL_SHAPES"]
    assert set(SCRAPERS) - set(DISABLED_ATS) <= set(shapes)
