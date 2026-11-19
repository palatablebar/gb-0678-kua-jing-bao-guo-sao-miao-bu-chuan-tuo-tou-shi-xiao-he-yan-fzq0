import sqlite3,json,hashlib
from datetime import datetime,timezone
from pathlib import Path
from app.config import DATA_DIR
from app.errors import DomainError

def connect():
    DATA_DIR.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(DATA_DIR/"records.db",timeout=20)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY,payload TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,decision TEXT NOT NULL DEFAULT '待复核')")
    db.execute("CREATE TABLE IF NOT EXISTS archives (id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL,version INTEGER NOT NULL,state TEXT NOT NULL,sha256 TEXT NOT NULL,content TEXT NOT NULL,run_version INTEGER NOT NULL,created_at TEXT NOT NULL,published_at TEXT,evidence_key TEXT NOT NULL DEFAULT '')")
    columns={row[1] for row in db.execute("PRAGMA table_info(archives)")}
    if "evidence_key" not in columns:db.execute("ALTER TABLE archives ADD COLUMN evidence_key TEXT NOT NULL DEFAULT ''")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS archives_one_published ON archives(run_id) WHERE state='published'")
    return db

def save(run_id,payload):
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO runs(id,payload) VALUES (?,?)",(run_id,json.dumps(payload,ensure_ascii=False)))
    return get(run_id)

def get(run_id):
    with connect() as db: row=db.execute("SELECT payload,version,decision FROM runs WHERE id=?",(run_id,)).fetchone()
    if not row: raise DomainError("NOT_FOUND","批次不存在",404)
    return {**json.loads(row[0]),"version":row[1],"decision":row[2]}

def latest():
    with connect() as db: row=db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
    return get(row[0]) if row else None

def history():
    with connect() as db: return [{"run_id":i,"version":v,"decision":d} for i,v,d in db.execute("SELECT id,version,decision FROM runs ORDER BY rowid DESC")]

def review(run_id,version,decision):
    if decision not in {"已复核","退回复核"}: raise DomainError("BAD_DECISION","复核状态无效")
    with connect() as db:
        cursor=db.execute("UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?",(decision,run_id,version))
        if cursor.rowcount!=1:
            if not db.execute("SELECT 1 FROM runs WHERE id=?",(run_id,)).fetchone(): raise DomainError("NOT_FOUND","批次不存在",404)
            raise DomainError("STALE_VERSION","版本已更新，请刷新",409)
    return get(run_id)

def _archive_row(row):
    return {"version":row[0],"state":row[1],"sha256":row[2],"run_version":row[3],"created_at":row[4],"published_at":row[5]}

def archive_versions(run_id):
    with connect() as db:
        rows=db.execute("SELECT version,state,sha256,run_version,created_at,published_at FROM archives WHERE run_id=? ORDER BY version DESC",(run_id,)).fetchall()
    return [_archive_row(row) for row in rows]

def published_archive(run_id):
    with connect() as db:
        row=db.execute("SELECT version,sha256,content,run_version,published_at FROM archives WHERE run_id=? AND state='published'",(run_id,)).fetchone()
    if not row:return None
    return {"version":row[0],"sha256":row[1],"content":row[2],"run_version":row[3],"published_at":row[4]}

def publish_archive(run_id,run_version,evidence,render):
    """原子发布证据版本。

    evidence 是不含版本戳的正式证据正文，用于判断内容是否变化；render(version, at)
    在事务内渲染带版本戳的可下载文本。渲染失败、版本冲突或写入失败都会回滚，
    旧发布版保持 published，绝不出现没有可读版本的窗口。内容未变化时复用现有版本，
    重复点击、补传重试都不产生重复归档。
    """
    now=datetime.now(timezone.utc).isoformat()
    key=hashlib.sha256(evidence.encode("utf-8")).hexdigest()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row=db.execute("SELECT version FROM runs WHERE id=?",(run_id,)).fetchone()
        if not row:
            db.rollback();raise DomainError("NOT_FOUND","批次不存在",404)
        if row[0]!=run_version:
            db.rollback();raise DomainError("STALE_VERSION","批次版本已更新，请刷新后再发布",409)
        previous=db.execute("SELECT version,sha256,content,evidence_key FROM archives WHERE run_id=? AND state='published'",(run_id,)).fetchone()
        if previous and previous[3]==key:
            db.rollback()
            return {"run_id":run_id,"version":previous[0],"state":"published","sha256":previous[1],"run_version":run_version,"published_at":now,"reused":True,"content":previous[2]}
        next_version=db.execute("SELECT COALESCE(MAX(version),0)+1 FROM archives WHERE run_id=?",(run_id,)).fetchone()[0]
        content=render(next_version,now)
        sha=hashlib.sha256(content.encode("utf-8")).hexdigest()
        db.execute("UPDATE archives SET state='superseded' WHERE run_id=? AND state='published'",(run_id,))
        db.execute("INSERT INTO archives(run_id,version,state,sha256,content,run_version,created_at,published_at,evidence_key) VALUES (?,?,?,?,?,?,?,?,?)",
                   (run_id,next_version,"published",sha,content,run_version,now,now,key))
    return {"run_id":run_id,"version":next_version,"state":"published","sha256":sha,"run_version":run_version,"published_at":now,"reused":False,"content":content}

def archive_content(run_id,version):
    with connect() as db:
        row=db.execute("SELECT content,sha256,run_version,published_at FROM archives WHERE run_id=? AND version=?",(run_id,version)).fetchone()
    if not row:raise DomainError("NOT_FOUND","证据版本不存在",404)
    return {"content":row[0],"sha256":row[1],"run_version":row[2],"published_at":row[3]}
