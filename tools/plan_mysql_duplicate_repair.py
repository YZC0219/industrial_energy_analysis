"""Prepare a reviewable duplicate repair plan without changing existing rows."""
import hashlib
import json
from collections import defaultdict
from datetime import datetime
from tools.verify_cdc_scheduler_runtime import source_rows, ROOT


def main():
    rows = source_rows('industrial_energy')
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row[k] for k in ('record_date', 'workshop_code', 'energy_code'))].append(row)
    repairs = []
    conflicts = []
    for key, versions in sorted(groups.items()):
        if len(versions) < 2:
            continue
        images = {json.dumps({k: v for k, v in row.items() if k != 'id'}, sort_keys=True,
                             ensure_ascii=False) for row in versions}
        ids = sorted(row['id'] for row in versions)
        item = {'business_key': list(key), 'keep_id': ids[0], 'redundant_ids': ids[1:]}
        (repairs if len(images) == 1 else conflicts).append(item)
    plan = {'status': 'review_only_no_source_changes', 'database': 'industrial_energy',
            'table': 'fact_energy_consumption', 'source_rows': len(rows),
            'distinct_business_keys': len(groups),
            'source_sha256': hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False,
                                                     separators=(',', ':')).encode()).hexdigest(),
            'redundant_rows': sum(len(item['redundant_ids']) for item in repairs),
            'conflicting_groups': conflicts, 'repairs': repairs,
            'rule': 'only rows identical in every field except id; retain smallest existing id',
            'execution_requirements': ['explicit user approval to delete existing data',
                                       'pause writers for maintenance window',
                                       'recheck complete source checksum and inbound references',
                                       'backup full original table before deleting',
                                       'transactional delete and post-repair uniqueness/metric verification'],
            'applied': False}
    path = ROOT / ('output/mysql_cdc_business_duplicate_plan_' + datetime.now().strftime('%Y%m%d%H%M%S') + '.json')
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(plan, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print('DUPLICATE_REPAIR_PLAN_READY', plan['redundant_rows'], path)


if __name__ == '__main__':
    main()
