import psycopg
from psycopg.rows import dict_row


def connect():
    # libpq 读取 PGHOST/PGSERVICE、SSL 选项和本机密码文件。
    return psycopg.connect(connect_timeout=8, row_factory=dict_row, options='-c timezone=UTC')
