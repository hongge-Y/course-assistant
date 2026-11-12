"""数据库初始化：备份原始数据、安装业务规则触发器。

用法：
    python init_db.py            # 备份（若尚无备份）并安装触发器
    python init_db.py --restore  # 用备份恢复演示数据（会覆盖当前数据库，需自行确认）
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / 'data' / 'course_selection.sqlite3'
BACKUP_PATH = ROOT / 'data' / 'course_selection.initial.sqlite3'

TRIGGERS = {
    'trg_course_capacity': """
        CREATE TRIGGER IF NOT EXISTS trg_course_capacity
        BEFORE INSERT ON enrollments
        WHEN (SELECT COUNT(*) FROM enrollments WHERE course_id = NEW.course_id)
             >= (SELECT capacity FROM courses WHERE course_id = NEW.course_id)
        BEGIN
            SELECT RAISE(ABORT, '课程已满：人数上限 20（触发器保护）');
        END;
    """,
    'trg_user_limit': """
        CREATE TRIGGER IF NOT EXISTS trg_user_limit
        BEFORE INSERT ON enrollments
        WHEN (SELECT COUNT(*) FROM enrollments WHERE user_id = NEW.user_id) >= 2
        BEGIN
            SELECT RAISE(ABORT, '每人最多选 2 门课程（触发器保护）');
        END;
    """,
}


def install_triggers(db_path: Path = DB_PATH) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute('PRAGMA foreign_keys = ON')
        for ddl in TRIGGERS.values():
            conn.executescript(ddl)
        conn.commit()
        return [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name")]
    finally:
        conn.close()


def main(argv: list[str]) -> int:
    if '--restore' in argv:
        if not BACKUP_PATH.exists():
            print('没有找到备份文件，无法恢复。')
            return 1
        shutil.copy2(BACKUP_PATH, DB_PATH)
        print(f'已用 {BACKUP_PATH.name} 覆盖 {DB_PATH.name}（恢复后需再运行一次本脚本以安装触发器）')
        return 0

    if '--reset-demo' in argv:
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute('PRAGMA foreign_keys = ON')
            removed = conn.execute(
                "DELETE FROM enrollments WHERE user_id IN ('S001','S002','S003')").rowcount
            conn.commit()
        finally:
            conn.close()
        print(f'已清空演示用户 S001—S003 的选课记录（{removed} 行），保留 C004 满员数据')
        names = install_triggers()
        print('触发器：', ', '.join(names) if names else '（无）')
        return 0

    if not BACKUP_PATH.exists():
        shutil.copy2(DB_PATH, BACKUP_PATH)
        print(f'已备份初始数据库 → {BACKUP_PATH.name}')
    else:
        print(f'备份已存在，跳过备份：{BACKUP_PATH.name}')

    names = install_triggers()
    print('已安装触发器：', ', '.join(names) if names else '（无）')
    conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    try:
        for course_id, name, capacity, enrolled in conn.execute(
            'SELECT c.course_id, c.name, c.capacity, COUNT(e.enrollment_id) '
            'FROM courses c LEFT JOIN enrollments e ON e.course_id = c.course_id '
            'GROUP BY c.course_id ORDER BY c.course_id'
        ):
            print(f'  {course_id} {name}: {enrolled}/{capacity}')
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
