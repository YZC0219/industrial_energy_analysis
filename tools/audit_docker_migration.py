"""Read-only inventory of Docker Desktop ext4/VHDX; never prints container secrets."""
import sys,json,stat,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tmp/docker-audit-libs'))
from dissect.hypervisor.disk.vhdx import VHDX
from dissect.extfs.extfs import ExtFS

def walk(node,prefix=''):
 for n in node.iterdir():
  if n.filename in ('.','..'):continue
  p=prefix+'/'+n.filename
  if n.filetype==stat.S_IFDIR:yield from walk(n,p)
  elif n.filetype==stat.S_IFREG:yield p,n

def audit(path):
 fs=ExtFS(VHDX(open(path,'rb')));containers=[];used={}
 for entry in fs.get('/data/docker/containers').iterdir():
  if entry.filename in ('.','..'):continue
  try:
   config=json.loads(fs.get('/data/docker/containers/'+entry.filename+'/config.v2.json').open().read())
   mounts=config.get('MountPoints',{})
   names=[m.get('Name') for m in mounts.values() if m.get('Name')]
   item={'name':config.get('Name'),'image':config.get('Config',{}).get('Image'),'volumes':names}
   containers.append(item)
   for name in names:used.setdefault(name,[]).append(item['name'])
  except Exception as e:containers.append({'id':entry.filename,'error':type(e).__name__})
 volumes=[]
 for n in fs.get('/data/docker/volumes').iterdir():
  if n.filename in ('.','..') or n.filetype!=stat.S_IFDIR:continue
  files=list(walk(n));total=sum(x.size for _,x in files)
  sig=hashlib.sha256()
  # Byte-exact comparison of volume contents, with relative paths included.
  for p,f in sorted(files,key=lambda x:x[0]):
   h=hashlib.sha256();stream=f.open()
   for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
   sig.update(p.encode());sig.update(b'\0');sig.update(h.digest())
  volumes.append({'name':n.filename,'bytes':total,'files':len(files),'content_signature':sig.hexdigest(),'containers':used.get(n.filename,[]),'top_entries':[x.filename for x in n.iterdir() if x.filename not in ('.','..')]})
 groups={}
 for v in volumes:groups.setdefault(v['content_signature'],[]).append(v['name'])
 return {'source':str(path),'containers':containers,'volumes':volumes,'identical_volume_groups':[v for v in groups.values() if len(v)>1]}

if __name__=='__main__':
 result=[]
 for path in ['D:/Docker/wsl/disk/docker_data.vhdx','D:/Docker/backup/docker_data.vhdx.pre-reinstall-20260925']:
  try:result.append(audit(path))
  except Exception as e:result.append({'source':path,'error':type(e).__name__})
 out=ROOT/'output/docker_migration_audit_20261103.json';out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
 for r in result:
  print(r['source'],r.get('error',''))
  for v in r.get('volumes',[]):print(v['name'],round(v['bytes']/1024**2,2),'MiB',v['files'],'files','users:',','.join(v['containers']))
  print('identical groups:',r.get('identical_volume_groups'))
