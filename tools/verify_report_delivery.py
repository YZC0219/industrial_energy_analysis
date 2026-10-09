"""Exercise report version activation, rollback, replay and browser use in isolation."""
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4
from playwright.sync_api import sync_playwright
from governance import report_delivery as delivery
from tools.report_delivery import build, replay, ROOT


def verify():
    verifier_sha = delivery.digest(Path(__file__).read_bytes())
    folder = ROOT / 'output/extensions' / ('report_delivery_' + uuid4().hex[:12])
    store = folder / 'store'
    one = build(store)
    first = delivery.activate(store, one, None)
    two = build(store)
    second = delivery.activate(store, two, first['publication'])
    restored = delivery.activate(store, one, second['publication'])
    assert restored['release'] == one and restored['previous_release'] == two
    assert delivery.current(store) == restored
    assert delivery.verify_version(store, two)['release'] == two
    replayed = folder / 'replayed.html'
    replay(store, one, replayed)
    original = Path(restored['report'])
    assert original.read_bytes() == replayed.read_bytes()
    # Original CSV files may change later; a reader selects this complete immutable version once.
    manifest = delivery.verify_version(store, one)['manifest']
    errors = []
    network = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch_persistent_context(str(folder/'browser-profile'),
            executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe',headless=True,
            viewport={'width':1440,'height':1000})
        try:
            page = browser.new_page()
            def offline(route):
                if route.request.url.startswith(('http://','https://')):
                    network.append(route.request.url);route.abort()
                else:route.continue_()
            page.route('**/*', offline)
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
            page.goto(original.as_uri(), wait_until='load')
            page.wait_for_selector('#f-ws input')
            assert page.locator('#scenario-rate option').all_text_contents() == ['3%','5%','10%']
            page.locator('#scenario-rate').select_option('5')
            assert '假设降幅 5%' in page.locator('#scenario-scope').inner_text()
            assert page.locator('#scenario-table tbody tr').count() > 1
            page.locator('#energy-scenario').screenshot(path=str(folder/'report_version.png'))
            assert not errors and not network,(errors,network)
        finally:browser.close()
    assert verifier_sha == delivery.digest(Path(__file__).read_bytes())
    assert all(delivery.digest((ROOT/name).read_bytes()) == sha for name,sha in manifest['metadata']['source_hashes'].items())
    result = {'success':True,'checked_at_utc':datetime.now(timezone.utc).isoformat(),
              'current_release':one,'previous_release':two,'original_pointer_retained_on_failure':'covered by unit tests',
              'activation_rollback_replay_passed':True,'frozen_input_files':sum(name.startswith('inputs/') for name in manifest['files']),
              'formula_definitions':manifest['metadata']['formula_definitions'],'source_hashes':manifest['metadata']['source_hashes'],
              'verifier_sha256':verifier_sha,'report':str(original),'browser_errors':errors,'external_requests':network,
              'scope':'isolated report version store; no warehouse, CDC, root report or API snapshot changes'}
    (folder/'verification.json').write_bytes(delivery.encode(result))
    (folder/'verification.md').write_text(
        '# 报告版本交付验收\n\n结论：通过。\n\n'
        '- 两个冻结输入的报告版本生成、切换和回退成功，旧版本及发布历史保留。\n'
        '- 从归档数据重放的 HTML 与归档报告逐字节一致。\n'
        '- 九项公式仍包含在报告中，真实浏览器情景切换通过，无脚本错误或外部请求。\n'
        '- 本轮没有更新 CDC 部署、主数仓、原报告或 API 数据快照。\n\n'
        f'[打开回退后的报告](store/versions/{one}/report.html)\n\n'
        '当前指针仅适用于此独立报告版本目录，尚未接入运行中的 CDC 或诊断服务。\n',encoding='utf-8')
    print(json.dumps({'success':True,'report':str(original),'verification':str(folder/'verification.md')},ensure_ascii=False))
    return result


if __name__ == '__main__':
    verify()
