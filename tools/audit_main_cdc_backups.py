"""Verify retained HDFS and Derby backups after the main CDC cutover."""
import argparse
import hashlib
import json
import re
import subprocess
from tools.verify_mysql_cdc_probe import ROOT


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--stamp', required=True)
    args = p.parse_args()
    if not re.fullmatch('[0-9]{14}', args.stamp):
        raise ValueError('Invalid backup timestamp')
    hdfs = '/warehouse/energy_cdc_main_backup_' + args.stamp
    remote = '/home/yzc/industrial_energy_cdc_main_backup_' + args.stamp + '/metastore.tar.gz'
    ssh = ['ssh', '-i', 'C:/Users/32074/.ssh/id_ed25519_github_yzc0219', '-o', 'IdentitiesOnly=yes',
           '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', 'yzc@192.168.21.131']
    def query(command):
        return subprocess.run([*ssh, command], cwd=ROOT, check=True, capture_output=True,
                              text=True, encoding='utf-8').stdout
    health = query('/usr/local/hadoop/bin/hdfs fsck ' + hdfs)
    if f"The filesystem under path '{hdfs}' is HEALTHY" not in health:
        raise ValueError('HDFS backup is not healthy')
    counts = {}
    for line in query('/usr/local/hadoop/bin/hdfs dfs -count ' + hdfs + '/*').splitlines():
        directories, files, size, path = line.split()
        counts[path] = {'directories': int(directories), 'files': int(files), 'bytes': int(size)}
    if len(counts) != 4:
        raise ValueError('All four warehouse layers must be backed up')
    checksum = query('sha256sum ' + remote).split()[0]
    local = ROOT / f'output/mysql_cdc_business_main_metastore_{args.stamp}.tar.gz'
    if hashlib.sha256(local.read_bytes()).hexdigest() != checksum:
        raise ValueError('Local metastore backup differs from VM backup')
    result = {'success': True, 'hdfs_backup': hdfs, 'hdfs_health': 'HEALTHY',
              'layer_counts': counts, 'remote_metastore': remote,
              'local_metastore': local.relative_to(ROOT).as_posix(), 'metastore_sha256': checksum,
              'scope': 'retained backup health and copy checksum; rollback was not executed'}
    output = ROOT / f'output/mysql_cdc_business_main_backup_audit_{args.stamp}.json'
    with output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print('MAIN_CDC_BACKUP_AUDIT_PASS', output)


if __name__ == '__main__':
    main()
