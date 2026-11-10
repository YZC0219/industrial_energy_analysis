"""Document ingestion isolated from energy fact tables."""
import csv
import hashlib
import io
import json
import os
import re
import xml.etree.ElementTree as ET

import pymysql
from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix='/api/v1/documents', tags=['多格式数据'])
LIMIT = 5 * 1024 * 1024
DDL = '''CREATE TABLE IF NOT EXISTS imported_documents (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
 filename VARCHAR(255) NOT NULL, source_format VARCHAR(8) NOT NULL,
 content_hash CHAR(64) NOT NULL UNIQUE,
 raw_content LONGTEXT NOT NULL, document JSON NOT NULL,
 created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4'''


def connect():
    from governance.mysql_source import connect as mysql_connect
    return mysql_connect(dict_rows=True)



def parse_document(filename, text):
    fmt = filename.rsplit('.', 1)[-1].lower()
    text = text.lstrip('\ufeff')
    if fmt == 'json':
        def invalid(value):
            raise ValueError('JSON 不允许 NaN 或 Infinity')
        value = json.loads(text, parse_constant=invalid)
    elif fmt == 'xml':
        if re.search(r'<!\s*(DOCTYPE|ENTITY)', text, re.I):
            raise ValueError('不支持 DTD 或实体声明')
        root = ET.fromstring(text)
        def element(e):
            return {'tag': e.tag, 'attributes': dict(e.attrib), 'text': e.text,
                    'tail': e.tail, 'children': [element(c) for c in e]}
        value = element(root)
    elif fmt == 'csv':
        reader = csv.reader(io.StringIO(text, newline=''), strict=True)
        fields = next(reader, None)
        if not fields or any(not x for x in fields) or len(set(fields)) != len(fields):
            raise ValueError('CSV 表头为空或重复')
        value = []
        for row in reader:
            if not row:
                continue
            if len(row) != len(fields):
                raise ValueError('CSV 列数不一致')
            value.append(dict(zip(fields, row)))
    else:
        raise ValueError('仅支持 JSON、XML、CSV')
    return fmt, value


def store(filename, text):
    fmt, value = parse_document(filename, text)
    digest = hashlib.sha256((fmt + '\0' + text).encode('utf-8')).hexdigest()
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(DDL)
            cur.execute('''INSERT INTO imported_documents
                (filename, source_format, content_hash, raw_content, document)
                VALUES (%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)''',
                (filename, fmt, digest, text, json.dumps(value, ensure_ascii=False, allow_nan=False)))
            ident = cur.lastrowid
        conn.commit()
        return {'id': ident, 'filename': filename, 'format': fmt, 'sha256': digest}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@router.post('')
async def import_document(request: Request, filename: str):
    if not filename or len(filename) > 255 or '/' in filename or '\\' in filename:
        raise HTTPException(400, '文件名无效')
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > LIMIT:
            raise HTTPException(413, '文件超过 5 MB')
    try:
        return await run_in_threadpool(store, filename, body.decode('utf-8-sig'))
    except (ValueError, UnicodeError, ET.ParseError, csv.Error, RecursionError):
        raise HTTPException(400, '文件格式无效，请检查编码、结构或嵌套深度')
    except pymysql.MySQLError:
        raise HTTPException(503, 'MySQL 写入失败，请检查连接、JSON 支持及建表权限')


@router.get('/{document_id}')
def read_document(document_id: int):
    try:
        conn = connect()
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT id,filename,source_format,raw_content,created_at FROM imported_documents WHERE id=%s', (document_id,))
                row = cur.fetchone()
            if row is None:
                raise HTTPException(404, '记录不存在')
            return row
        finally:
            conn.close()
    except pymysql.MySQLError:
        raise HTTPException(503, 'MySQL 读取失败或尚未导入数据')
