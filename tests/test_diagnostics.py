"""Workbench contracts: scoped evidence, persistent feedback and guarded answers."""
import json

import pytest
from fastapi.testclient import TestClient

from src.api import app
from test_api import seed_outputs


@pytest.fixture
def client(tmp_path, monkeypatch):
    seed_outputs(tmp_path)
    monkeypatch.setenv("ENERGY_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("ENERGY_DIAGNOSTICS_DB", str(tmp_path / "feedback.sqlite3"))
    monkeypatch.setenv("ENERGY_DIAGNOSTICS_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.delenv("LLM_API_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    return TestClient(app)


def test_workbench_scopes_evidence_and_paginates(client):
    assert client.get("/diagnostics").status_code == 200
    listing = client.get("/api/v1/diagnostics", params={"limit": 1}).json()
    assert listing["total"] == 2 and len(listing["items"]) == 1
    item = listing["items"][0]
    detail = client.get("/api/v1/diagnostics/" + item["id"]).json()
    assert all(r["车间"] == item["workshop"] for r in detail["evidence"])
    assert all(r["车间编码"] == item["workshop_code"] for r in detail["daily_context"])
    assert client.get("/api/v1/diagnostics", params={"date_from": "2026-01-01"}).json()["total"] == 0
    assert client.get("/api/v1/diagnostics", params={"date_from": "2026-01-01", "date_to": "2025-01-01"}).status_code == 422
    assert client.get("/api/v1/diagnostics/missing").status_code == 404


def test_feedback_retains_history_and_does_not_modify_csv(client, tmp_path):
    source = tmp_path / "Q16_单耗异常日检测_2sigma.csv"
    before = source.read_bytes()
    item = client.get("/api/v1/diagnostics", params={"workshop_code": "W01"}).json()["items"][0]
    url = "/api/v1/diagnostics/" + item["id"] + "/feedback"
    body = {"status": "confirmed", "operator": "操作员甲", "reason": "已核对计量", "action": ""}
    assert client.post(url, json={**body, "reason": " "}).status_code == 422
    assert client.post(url, json={**body, "operator": " "}).status_code == 422
    assert client.post(url, json=body).status_code == 200
    assert client.post(url, json={**body, "status": "resolved"}).status_code == 422
    assert client.post(url, json={**body, "status": "resolved", "action": "完成现场复核"}).status_code == 200
    detail = client.get(url.removesuffix("/feedback")).json()
    assert [h["status"] for h in detail["history"]] == ["resolved", "confirmed"]
    assert client.get("/api/v1/diagnostics", params={"status": "resolved"}).json()["total"] == 1
    assert source.read_bytes() == before


def test_answers_are_scoped_audited_and_reject_unverified_causes(client, tmp_path):
    item = client.get("/api/v1/diagnostics", params={"workshop_code": "W01"}).json()["items"][0]
    body = {"incident_id": item["id"], "question": "单耗异常的证据是什么"}
    reply = client.post("/api/v1/diagnostics/ask", json=body)
    assert reply.status_code == 200
    payload = reply.json()
    assert payload["mode"] == "offline_guarded"
    assert payload["claims"]
    assert all(c["evidence_value"]["车间"] == "熔炼车间"
               for claim in payload["claims"] for c in claim["citations"])
    audit = json.loads((tmp_path / "audit" / (payload["analysis_id"] + ".json")).read_text(encoding="utf-8"))
    assert audit["result"]["analysis_id"] == payload["analysis_id"]
    assert client.post("/api/v1/diagnostics/ask", json={**body, "question": "为什么发生设备故障"}).json()["insufficient_evidence"]
    assert client.post("/api/v1/diagnostics/ask", json={**body, "question": "W02 的证据是什么"}).json()["insufficient_evidence"]


def test_online_selection_is_guarded_and_failure_does_not_fallback(client, monkeypatch):
    monkeypatch.setenv("LLM_API_URL", "http://unused.invalid")
    monkeypatch.setenv("LLM_MODEL", "test")
    monkeypatch.setattr("src.diagnostics.openai_compatible_llm",
                        lambda _: {"insufficient_evidence": False, "evidence_ids": ["fabricated"]})
    response = client.post("/api/v1/diagnostics/ask", json={"question": "W01 单耗异常"})
    assert response.status_code == 503
    assert "未生成回答" in response.json()["detail"]


def test_monthly_questions_use_event_and_daily_records_instead_of_total(client):
    item = client.get('/api/v1/diagnostics', params={'workshop_code': 'W02'}).json()['items'][0]
    def ask(question):
        response = client.post('/api/v1/diagnostics/ask', json={'incident_id': item['id'], 'question': question})
        assert response.status_code == 200
        return response.json()
    result = ask('本次异常有哪些证据？')
    citations = [c for claim in result['claims'] for c in claim['citations']]
    assert citations and all('Q22_' in c['source_path'] for c in citations)
    result = ask('同期能源费用是多少？')
    citations = [c for claim in result['claims'] for c in claim['citations']]
    assert citations and all('Q28_' in c['source_path'] for c in citations)
    assert citations[0]['evidence_value']['能源费用_元'] == '50'
    assert result['answer_metrics'][0]['value'] == 50
    assert result['answer_metrics'][0]['record_days'] == 1
    assert ask('2025-02-01 的费用是多少？')['insufficient_evidence']
    assert ask('设备检修日期是哪天？')['insufficient_evidence']


def test_priority_summary_direction_and_trend_do_not_use_future_days(client):
    listing = client.get('/api/v1/diagnostics', params={'sort':'priority'}).json()
    assert listing['summary']['pending'] == 2
    assert listing['summary']['priority_open'] == 1
    assert listing['items'][0]['workshop_code'] == 'W02'
    assert client.get('/api/v1/diagnostics', params={'priority_only':True}).json()['total'] == 1
    daily = next(i for i in listing['items'] if i['period'] == 'day')
    assert daily['direction'] == '偏高'
    assert daily['deviation_pct'] == 66.67
    detail = client.get('/api/v1/diagnostics/' + daily['id']).json()
    assert [r['日期'] for r in detail['trend']] == ['2025-01-01', '2025-01-02']
    assert all(r['车间编码'] == 'W01' for r in detail['trend'])


def test_stale_feedback_conflicts_without_losing_history(client):
    item = client.get('/api/v1/diagnostics').json()['items'][0]
    url = '/api/v1/diagnostics/' + item['id'] + '/feedback'
    body = {'status':'confirmed','operator':'甲','reason':'核对完成','expected_revision':0}
    first = client.post(url, json=body)
    assert first.status_code == 200
    assert client.post(url, json={**body, 'operator':'乙'}).status_code == 409
    latest = first.json()['history'][0]['id']
    second = client.post(url, json={**body, 'operator':'乙','expected_revision':latest})
    assert second.status_code == 200 and len(second.json()['history']) == 2


def test_online_scoped_answer_rejects_mapping_as_answer(client, monkeypatch, tmp_path):
    from ml.attribution_assistant import load_evidence
    from src.diagnostics import ROOT
    evidence = load_evidence(ROOT / 'docs/指标字典.md', tmp_path)
    item = client.get('/api/v1/diagnostics', params={'workshop_code':'W01'}).json()['items'][0]
    monkeypatch.setenv('LLM_API_URL', 'http://unused.invalid')
    monkeypatch.setenv('LLM_MODEL', 'test')
    event = next(e for e in evidence if 'Q16_' in e.source_path)
    monkeypatch.setattr('src.diagnostics.openai_compatible_llm',
                        lambda _: {'insufficient_evidence':False, 'evidence_ids':[event.evidence_id]})
    body = {'question':'本次异常有哪些证据？', 'incident_id':item['id']}
    result = client.post('/api/v1/diagnostics/ask', json=body)
    assert result.status_code == 200 and result.json()['mode'] == 'configured_llm'
    mapping = next(e for e in evidence if 'Q03_' in e.source_path and e.evidence_value['车间编码'] == 'W01')
    monkeypatch.setattr('src.diagnostics.openai_compatible_llm',
                        lambda _: {'insufficient_evidence':False, 'evidence_ids':[mapping.evidence_id]})
    assert client.post('/api/v1/diagnostics/ask', json=body).status_code == 503


def test_model_status_does_not_expose_credentials_or_call_model(client, monkeypatch):
    monkeypatch.setenv('LLM_API_URL', 'https://example.invalid/v1/chat/completions')
    monkeypatch.setenv('LLM_MODEL', 'configured-test')
    monkeypatch.setenv('LLM_API_KEY', 'never-expose-test-secret')
    monkeypatch.setattr('src.diagnostics.openai_compatible_llm', lambda _: pytest.fail('status called model'))
    response = client.get('/api/v1/diagnostics-model')
    assert response.json()['state'] == 'configured_unverified'
    assert response.json()['key_present']
    assert 'never-expose' not in response.text and 'example.invalid' not in response.text


def test_invalid_configuration_fails_explicitly(client, monkeypatch):
    monkeypatch.setenv('LLM_API_URL', 'file:///private')
    monkeypatch.setenv('LLM_MODEL', 'test')
    assert client.get('/api/v1/diagnostics-model').json()['state'] == 'invalid_configuration'
    assert client.post('/api/v1/diagnostics/ask', json={'question':'异常证据'}).status_code == 503


def test_starter_preserves_process_settings_and_loads_only_allowed_keys(monkeypatch, tmp_path):
    from tools.start_diagnostics import configure
    env = tmp_path / '.env'
    env.write_text('LLM_API_URL=https://example.invalid/v1/chat/completions\nLLM_MODEL="file-model"\nLLM_API_KEY=test-secret\nUNRELATED_SECRET=not-loaded\n', encoding='utf-8')
    for key in ('LLM_API_URL','LLM_API_KEY','UNRELATED_SECRET'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('LLM_MODEL', 'process-model')
    monkeypatch.setenv('LLM_TIMEOUT_SECONDS', '90')
    # Track keys configure may mutate so pytest restores the process afterwards.
    monkeypatch.setenv('LLM_API_URL', '')
    monkeypatch.setenv('LLM_API_KEY', '')
    import os
    assert configure(env, 'online')['configured']
    assert os.environ['LLM_MODEL'] == 'process-model'
    assert 'UNRELATED_SECRET' not in os.environ
    assert configure(env, 'offline')['state'] == 'offline'


def test_starter_online_requires_configuration(monkeypatch, tmp_path):
    from tools.start_diagnostics import configure
    monkeypatch.delenv('LLM_API_URL', raising=False)
    monkeypatch.delenv('LLM_MODEL', raising=False)
    with pytest.raises(ValueError, match='在线模式缺少配置'):
        configure(tmp_path / 'missing.env', 'online')


def test_deepseek_without_key_keeps_offline_workbench_available(client, monkeypatch):
    monkeypatch.setenv('LLM_API_URL', 'https://api.deepseek.com/chat/completions')
    monkeypatch.setenv('LLM_MODEL', 'deepseek-flash')
    monkeypatch.delenv('LLM_API_KEY', raising=False)
    assert client.get('/api/v1/diagnostics-model').json()['state'] == 'missing_credentials'
    item = client.get('/api/v1/diagnostics').json()['items'][0]
    response = client.post('/api/v1/diagnostics/ask', json={'incident_id':item['id'], 'question':'本次异常有哪些证据？'})
    assert response.status_code == 200 and response.json()['mode'] == 'offline_guarded'


def test_online_monthly_context_keeps_event_and_all_daily_records(client, monkeypatch, tmp_path):
    from datetime import date, timedelta
    path = tmp_path / 'Q28_各车间日度能耗与产量.csv'
    path.write_text('日期,车间编码,车间,综合能耗_tce,能源费用_元,碳排放_tCO2,产量\n' +
                   ''.join(f'{date(2025,1,1)+timedelta(days=i)},W02,装配车间,1,2,3,4\n'
                           for i in range(31)), encoding='utf-8-sig')
    monkeypatch.setenv('LLM_API_URL','https://example.invalid/v1/chat/completions')
    monkeypatch.setenv('LLM_MODEL','test')
    def model(prompt):
        candidates = json.loads(prompt.split('\nEVIDENCE=',1)[1])
        daily = [e for e in candidates if 'Q28_' in e['source_path']]
        assert len(daily) == 31
        assert any('Q22_' in e['source_path'] for e in candidates)
        assert not any('Q24_' in e['source_path'] for e in candidates)
        return {'insufficient_evidence':False,'evidence_ids':[e['evidence_id'] for e in daily]}
    monkeypatch.setattr('src.diagnostics.openai_compatible_llm',model)
    item=client.get('/api/v1/diagnostics',params={'workshop_code':'W02'}).json()['items'][0]
    response=client.post('/api/v1/diagnostics/ask',json={'incident_id':item['id'],'question':'同期能源费用是多少？'})
    assert response.status_code == 200
    assert response.json()['answer_metrics'][0]['value'] == 62
    assert response.json()['answer_metrics'][0]['record_days'] == 31
