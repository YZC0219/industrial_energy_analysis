"""Report publication failures must retain a complete, usable prior version."""
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from governance import report_delivery as delivery


def version(root, label):
    return delivery.create_version(root, {
        'report.html': ('<!doctype html><p>'+label+'</p>').encode(),
        'report_data.json': delivery.encode({'label':label}),
        'analysis_contract.json': delivery.encode({'version':1}),
    }, {'label':label})


def test_activation_and_rollback_keep_both_versions_intact(tmp_path):
    one,two=version(tmp_path,'first'),version(tmp_path,'second')
    first=delivery.activate(tmp_path,one,None)
    second=delivery.activate(tmp_path,two,first['publication'])
    restored=delivery.activate(tmp_path,one,second['publication'])
    assert restored['release']==one and restored['previous_release']==two
    assert delivery.verify_version(tmp_path,two)['manifest']['metadata']['label']=='second'
    assert len(list((tmp_path/'history').glob('*.json')))==3


def test_incomplete_version_cannot_replace_the_current_report(tmp_path):
    first=delivery.activate(tmp_path,version(tmp_path,'accepted'),None)
    incomplete=delivery.reserve_version(tmp_path)
    with pytest.raises(FileNotFoundError):delivery.activate(tmp_path,incomplete,first['publication'])
    assert delivery.current(tmp_path)==first


def test_tampered_artifact_blocks_activation_and_keeps_pointer(tmp_path):
    first=delivery.activate(tmp_path,version(tmp_path,'accepted'),None)
    candidate=version(tmp_path,'candidate')
    (delivery.version_folder(tmp_path,candidate)/'report.html').write_text('changed')
    with pytest.raises(ValueError,match='integrity'):delivery.activate(tmp_path,candidate,first['publication'])
    assert delivery.current(tmp_path)==first


def test_manifest_change_is_detected_by_the_current_pointer(tmp_path):
    first=delivery.activate(tmp_path,version(tmp_path,'accepted'),None)
    path=delivery.version_folder(tmp_path,first['release'])/'manifest.json'
    payload=json.loads(path.read_bytes());payload['metadata']['label']='changed'
    path.write_bytes(delivery.encode(payload))
    with pytest.raises(ValueError,match='manifest'):delivery.current(tmp_path)


def test_failed_pointer_replace_preserves_old_report(tmp_path,monkeypatch):
    first=delivery.activate(tmp_path,version(tmp_path,'accepted'),None)
    candidate=version(tmp_path,'candidate')
    def failed_replace(*args):raise OSError('simulated interruption before pointer replacement')
    monkeypatch.setattr(delivery.os,'replace',failed_replace)
    with pytest.raises(OSError):delivery.activate(tmp_path,candidate,first['publication'])
    assert delivery.current(tmp_path)==first
    assert delivery.verify_version(tmp_path,candidate)['release']==candidate


def test_concurrent_publishers_cannot_overwrite_each_other_silently(tmp_path):
    first=delivery.activate(tmp_path,version(tmp_path,'accepted'),None)
    candidates=[version(tmp_path,'a'),version(tmp_path,'b')]
    def publish(candidate):
        try:return delivery.activate(tmp_path,candidate,first['publication'])['release']
        except ValueError:return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as executor:results=list(executor.map(publish,candidates))
    assert results.count('conflict')==1
    assert delivery.current(tmp_path)['release'] in candidates


@pytest.mark.parametrize('name',['../outside.html','/outside.html','C:/outside.html','a\\b.html','a/../b.html'])
def test_manifest_paths_cannot_escape_the_version_directory(tmp_path,name):
    with pytest.raises(ValueError):delivery.safe_file(tmp_path,name)


def test_unlisted_file_prevents_activation(tmp_path):
    candidate=version(tmp_path,'candidate')
    (delivery.version_folder(tmp_path,candidate)/'unlisted.txt').write_text('not in manifest')
    with pytest.raises(ValueError,match='Unexpected'):delivery.activate(tmp_path,candidate,None)
    assert delivery.current(tmp_path) is None


def test_published_version_cannot_be_completed_again(tmp_path):
    candidate=version(tmp_path,'candidate')
    with pytest.raises(ValueError,match='unfinished'):
        delivery.complete_version(tmp_path,candidate,{name:b'changed' for name in delivery.REQUIRED},{})


def test_pointer_with_missing_history_is_not_a_valid_current_report(tmp_path):
    pointer={'format_version':1,'publication':'a'*32,'release':'report_'+'b'*16,'manifest_sha256':'c'*64}
    (tmp_path/'current.json').write_bytes(delivery.encode(pointer))
    with pytest.raises(FileNotFoundError):delivery.current(tmp_path)


def test_replay_retains_recorded_json_key_order(tmp_path):
    from src.make_report import render
    from tools.report_delivery import replay, ROOT
    data={'totals':{},'z':1,'a':2}
    html=render(data).encode('utf-8')
    root=tmp_path/'store'
    release=delivery.create_version(root,{
        'report.html':html,'report_data.json':json.dumps(data).encode(),
        'analysis_contract.json':b'{}',
    },{'source_hashes':{'src/make_report.py':delivery.digest((ROOT/'src/make_report.py').read_bytes())}})
    output=tmp_path/'replayed.html'
    replay(root,release,output)
    assert output.read_bytes()==html
    with pytest.raises(ValueError,match='outside'):replay(root,release,delivery.version_folder(root,release)/'extra.html')


def test_replay_does_not_claim_reproducibility_with_changed_source(tmp_path):
    from tools.report_delivery import replay
    release=delivery.create_version(tmp_path/'store',{
        'report.html':b'<p>archive</p>','report_data.json':b'{}','analysis_contract.json':b'{}',
    },{'source_hashes':{'src/make_report.py':'0'*64}})
    output=tmp_path/'replayed.html'
    with pytest.raises(ValueError,match='recorded source'):replay(tmp_path/'store',release,output)
    assert not output.exists()
