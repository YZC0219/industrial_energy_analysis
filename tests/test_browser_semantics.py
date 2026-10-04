from copy import deepcopy
import pytest
from governance import browser
from src.make_report import render


def test_contract_uses_all_seven_registry_scalar_definitions():
    result = browser.contract()
    assert len(result['metrics']) == 7
    intensity = next(m for m in result['metrics'] if m['id'] == 'energy_intensity_kgce')
    assert intensity['scope'] == 'single_workshop_only'
    assert intensity['exclude_process'] == '公用工程'
    assert intensity['field'] == 'tce' and intensity['denominator_field'] == 'qty'
    assert 'prod' not in intensity
    assert len(result['source_sql_sha256']) == 64


def test_unsupported_daily_scope_requires_review(monkeypatch):
    config = deepcopy(browser.catalog())
    config['metrics'][0]['source_query'] = 'Q01'
    monkeypatch.setattr(browser, 'catalog', lambda: config)
    with pytest.raises(ValueError, match='reviewed'):
        browser.contract()


def test_render_embeds_engine_and_registry_without_changing_payload():
    html = render({'totals': {}})
    assert 'window.ENERGY_SEMANTIC_CONTRACT =' in html
    assert 'root.EnergySemantic' in html
    assert '/*__SEMANTIC_ENGINE__*/' not in html


def test_offline_report_does_not_depend_on_remote_font_stylesheets():
    html = render({'totals': {}})
    assert 'fonts.googleapis.com' not in html
    assert 'fonts.gstatic.com' not in html
