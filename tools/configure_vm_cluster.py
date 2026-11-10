"""Configure isolated HDFS/Spark services on the two retained VMware nodes."""
import sys,json,shlex
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tmp/docker-audit-libs'))
import paramiko
BASE='/home/yzc/energy-cluster-20261103'
NODES=['192.168.21.131','192.168.21.132']
EXCLUDED_NODES=['192.168.21.133']

def client(ip):
 c=paramiko.SSHClient();c.load_host_keys('C:/Users/32074/.ssh/known_hosts')
 if ip!=NODES[0]:
  for kind,key in c.get_host_keys()[NODES[0]].items():c.get_host_keys().add(ip,kind,key)
 c.connect(ip,username='yzc',key_filename='C:/Users/32074/.ssh/id_ed25519_github_yzc0219',look_for_keys=False,allow_agent=False,timeout=10)
 return c

def run(c,command,timeout=120):
 _,o,e=c.exec_command(command,timeout=timeout);stdout=o.read().decode();stderr=e.read().decode();code=o.channel.recv_exit_status()
 if code:raise RuntimeError(str(code)+' '+command+'\n'+stdout+'\n'+stderr)
 return stdout+stderr

def xml(props):
 return '<configuration>'+''.join('<property><name>'+k+'</name><value>'+str(v)+'</value></property>' for k,v in props.items())+'</configuration>\n'

def configure():
 for ip in NODES:
  c=client(ip);run(c,f'mkdir -p {BASE}/conf {BASE}/spark-conf {BASE}/logs {BASE}/pids {BASE}/storage {BASE}/spark-work')
  java=run(c,'readlink -f $(command -v java)').strip().removesuffix('/bin/java')
  files={'conf/core-site.xml':xml({'fs.defaultFS':'hdfs://192.168.21.131:19000','hadoop.tmp.dir':BASE+'/tmp'}),
   'conf/hdfs-site.xml':xml({'dfs.replication':1,'dfs.datanode.synconclose':'true','dfs.hosts':BASE+'/conf/workers','dfs.hosts.exclude':BASE+'/conf/workers.exclude','dfs.namenode.name.dir':'file://'+BASE+'/storage/namenode','dfs.datanode.data.dir':'file://'+BASE+'/storage/datanode','dfs.namenode.rpc-address':'192.168.21.131:19000','dfs.namenode.http-address':'192.168.21.131:19870','dfs.datanode.address':'0.0.0.0:19866','dfs.datanode.http.address':'0.0.0.0:19864','dfs.datanode.ipc.address':'0.0.0.0:19867','dfs.datanode.hostname':ip}),
   'conf/workers':'\n'.join(NODES[1:])+'\n',
   'conf/workers.exclude':'\n'.join(EXCLUDED_NODES)+'\n',
   'conf/hadoop-env.sh':f'export JAVA_HOME={java}\nexport HADOOP_HEAPSIZE_MAX=384\nexport HADOOP_LOG_DIR={BASE}/logs\nexport HADOOP_PID_DIR={BASE}/pids\n',
   'spark-conf/spark-env.sh':f'export JAVA_HOME={java}\nexport SPARK_LOCAL_IP={ip}\nexport SPARK_LOG_DIR={BASE}/logs\nexport SPARK_PID_DIR={BASE}/pids\nexport SPARK_WORKER_DIR={BASE}/spark-work\nexport SPARK_DAEMON_MEMORY=256m\nexport HADOOP_CONF_DIR={BASE}/conf\n',
   'spark-conf/spark-defaults.conf':'spark.master spark://192.168.21.131:7077\nspark.driver.host 192.168.21.131\nspark.executor.memory 768m\nspark.executor.cores 1\nspark.cores.max 1\nspark.sql.shuffle.partitions 8\nspark.ui.port 14040\n',
   'spark-conf/workers':'\n'.join(NODES[1:])+'\n',
   'env.sh':f'export HADOOP_HOME=/usr/local/hadoop\nexport SPARK_HOME=/usr/local/spark\nexport HADOOP_CONF_DIR={BASE}/conf\nexport SPARK_CONF_DIR={BASE}/spark-conf\nexport PATH=/usr/local/hadoop/bin:/usr/local/spark/bin:$PATH\n'}
  sftp=c.open_sftp()
  for name,text in files.items():
   with sftp.file(BASE+'/'+name,'w') as f:f.write(text)
  sftp.close();print('Configured',ip,flush=True);c.close()
 c=client(NODES[0]);prefix=f'. {BASE}/env.sh; '
 print(run(c,prefix+f'if test ! -f {BASE}/storage/namenode/current/VERSION; then hdfs namenode -format -nonInteractive energy-two-node-20261103; fi'),flush=True)
 print(run(c,prefix+'hdfs --daemon start namenode; SPARK_MASTER_HOST=192.168.21.131 SPARK_MASTER_WEBUI_PORT=18080 /usr/local/spark/sbin/start-master.sh'),flush=True);c.close()
 for ip in NODES[1:]:
  c=client(ip);print(run(c,prefix+f'hdfs --daemon start datanode; /usr/local/spark/sbin/start-worker.sh --host {ip} --cores 1 --memory 1g --webui-port 18081 spark://192.168.21.131:7077'),flush=True);c.close()

if __name__=='__main__':configure()
