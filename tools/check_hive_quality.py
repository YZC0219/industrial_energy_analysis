"""Fail-fast DWD/DWS quality gate. Queries are intentionally small and partition-pruned."""
from __future__ import annotations
import argparse, subprocess

CHECKS = {
 "dwd_batch_covered": "SELECT count(*)=0 FROM energy_ods.ods_energy_consumption o LEFT ANTI JOIN energy_dwd.dwd_energy_consumption_merge_stage s ON s.run_dt='{d}' AND s.record_date=o.record_date AND s.workshop_code=o.workshop_code AND s.energy_code=o.energy_code WHERE o.dt='{d}'",
 "dwd_unique": "SELECT count(*)=count(DISTINCT energy_detail_key) FROM energy_dwd.dwd_energy_consumption_merge_stage WHERE run_dt='{d}'",
 "dwd_required": "SELECT count(*)=0 FROM energy_dwd.dwd_energy_consumption_merge_stage WHERE run_dt='{d}' AND (record_date IS NULL OR workshop_code IS NULL OR energy_code IS NULL OR consumption<0)",
 "dws_reconcile": "SELECT abs(coalesce(a.v,0)-coalesce(b.v,0))<0.01 FROM (SELECT sum(x.std_coal_kgce)/1000 v FROM energy_dwd.dwd_energy_consumption_detail x JOIN (SELECT DISTINCT target_dt FROM energy_dwd.dwd_energy_consumption_merge_stage WHERE run_dt='{d}') i ON x.dt=i.target_dt) a CROSS JOIN (SELECT sum(x.tce) v FROM energy_dws.dws_workshop_energy_day x JOIN (SELECT DISTINCT target_dt FROM energy_dwd.dwd_energy_consumption_merge_stage WHERE run_dt='{d}') i ON x.dt=i.target_dt) b",
}

def main():
 p=argparse.ArgumentParser(); p.add_argument('--biz-date',required=True); p.add_argument('--stage',choices=('dwd','dws'),required=True); a=p.parse_args()
 failures=[]
 selected={k:v for k,v in CHECKS.items() if (a.stage=='dws') == (k=='dws_reconcile')}
 for name,query in selected.items():
  r=subprocess.run(['spark-sql','--silent','-e',query.format(d=a.biz_date)],capture_output=True,text=True)
  values=[x.strip().lower() for x in r.stdout.splitlines() if x.strip().lower() in {'true','false'}]
  ok=r.returncode==0 and values and values[-1]=='true'
  print(f"QUALITY {name}={'PASS' if ok else 'FAIL'}")
  if not ok: failures.append(name)
 if failures: raise SystemExit('质量门禁失败: '+','.join(failures))

if __name__=='__main__': main()
