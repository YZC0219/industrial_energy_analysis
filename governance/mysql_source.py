"""Explicit MySQL backend for the existing allowlisted semantic SQL."""
import os
import pymysql


def enabled():
    backend = os.getenv('ENERGY_DATA_BACKEND', 'csv')
    if backend not in ('csv', 'mysql'):
        raise ValueError('ENERGY_DATA_BACKEND must be csv or mysql')
    return backend == 'mysql'


def connect(dict_rows=False):
    options = dict(host=os.getenv('MYSQL_HOST', '127.0.0.1'),
        port=int(os.getenv('MYSQL_PORT', '3307')), user=os.getenv('MYSQL_USER', 'root'),
        password=os.getenv('MYSQL_PWD', ''), database=os.getenv('MYSQL_DB', 'industrial_energy'),
        charset='utf8mb4', connect_timeout=5, read_timeout=60, write_timeout=30)
    if dict_rows:
        options['cursorclass'] = pymysql.cursors.DictCursor
    return pymysql.connect(**options)


def analytical_rows(entry, sql):
    conn = None
    try:
        conn = connect()
        with conn.cursor() as cursor:
            cursor.execute('START TRANSACTION READ ONLY')
            cursor.execute(sql)
            headers = [column[0] for column in cursor.description]
            if headers != [column['name'] for column in entry['columns']]:
                raise ValueError('MySQL result schema differs from semantic definition')
            return [dict(zip(headers, ['' if value is None else str(value) for value in row]))
                    for row in cursor.fetchall()]
    except pymysql.MySQLError as exc:
        raise OSError('MySQL analytical source unavailable') from exc
    finally:
        if conn is not None:
            conn.rollback()
            conn.close()
