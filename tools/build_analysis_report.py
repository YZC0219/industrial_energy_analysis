"""Generate a separate artifact using the canonical analytical report renderer."""
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from src import make_report
from governance.analysis_formulas import contract
from governance.analysis_report import integrate  # Compatibility for existing callers.

SOURCES = ('governance/analysis_formulas.yaml', 'governance/analysis_formulas.py',
           'src/analysis_formulas.js', 'governance/analysis_report.py', 'src/report_template.html', 'src/make_report.py',
           'governance/catalog.py', 'governance/browser.py', 'governance/consumers.py',
           'governance/metrics.yaml', 'src/semantic_metrics.js', 'tools/build_analysis_report.py')


def hashes():
    return {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in SOURCES}


def build():
    before=hashes()
    metadata=ROOT/'output/extensions/semantic_runtime.json'
    metadata_bytes=metadata.read_bytes()
    evidence=json.loads(metadata_bytes.decode('utf-8'))
    source=(ROOT/evidence['evidence_directory']).resolve()
    if not evidence.get('success') or not source.is_relative_to((ROOT/'output/extensions').resolve()):
        raise ValueError('Invalid semantic artifact source')
    prior=make_report.OUT_DIR
    try:
        make_report.OUT_DIR=str(source)
        data=make_report.build()
        html=make_report.render(data)
    finally:
        make_report.OUT_DIR=prior
    if hashes()!=before or metadata.read_bytes()!=metadata_bytes:
        raise ValueError('Concurrent source edits detected; rerun after edits settle')
    folder=ROOT/'output/extensions'/('analysis_formulas_'+uuid4().hex[:12])
    folder.mkdir(parents=True)
    (folder/'report.html').write_text(html,encoding='utf-8')
    manifest={'source_hashes':before,'contract_sha256':contract()['sha256'],
              'source_semantic_report_sha256':hashlib.sha256(metadata_bytes).hexdigest(),
              'html_sha256':hashlib.sha256((folder/'report.html').read_bytes()).hexdigest(),
              'scope':'separate report; canonical report rendering; existing deployments and main DAG untouched'}
    (folder/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return folder,data,manifest


if __name__=='__main__':
    folder,_,_=build()
    print(str(folder/'report.html'))
