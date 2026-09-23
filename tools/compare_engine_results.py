"""Compare pandas/MySQL/Spark exports and append reproducible performance evidence."""
from __future__ import annotations
import argparse, json, time
from pathlib import Path
import pandas as pd

def main():
 p=argparse.ArgumentParser()
 for e in ('pandas','mysql','spark'): p.add_argument(f'--{e}',required=True)
 p.add_argument('--keys',required=True,help='逗号分隔的主键列')
 p.add_argument('--metrics',required=True,help='逗号分隔的数值列')
 p.add_argument('--atol',type=float,default=0.01)
 p.add_argument('--report',default='output/engine_comparison.json')
 a=p.parse_args(); started=time.perf_counter(); keys=a.keys.split(','); metrics=a.metrics.split(',')
 frames={e:pd.read_csv(getattr(a,e)).sort_values(keys).reset_index(drop=True) for e in ('pandas','mysql','spark')}
 base=frames['pandas']; result={'rows':{},'comparisons':{},'atol':a.atol}
 failed=False
 for engine,df in frames.items(): result['rows'][engine]=len(df)
 for engine in ('mysql','spark'):
  df=frames[engine]; key_ok=base[keys].astype(str).equals(df[keys].astype(str)) if len(base)==len(df) else False
  diffs={m: (float((base[m].astype(float)-df[m].astype(float)).abs().max()) if key_ok and len(base) else None) for m in metrics}
  ok=key_ok and all(v is not None and v<=a.atol for v in diffs.values())
  result['comparisons'][engine]={'key_equal':key_ok,'max_abs_diff':diffs,'passed':ok}; failed|=not ok
 result['comparison_seconds']=round(time.perf_counter()-started,4)
 out=Path(a.report); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(result,ensure_ascii=False,indent=2)); raise SystemExit(1 if failed else 0)
if __name__=='__main__': main()
