"""
CRM日报平台 - 数据层
SQLite ORM操作，包含建表、CRUD、去重判断
"""
import sqlite3
import json
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


class Database:
    def __init__(self, db_path: str = "data/crm_brief.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_tables(self):
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    summary TEXT,
                    url TEXT NOT NULL,
                    source_name TEXT,
                    section TEXT NOT NULL CHECK(section IN ('A','B','C','D','E','F')),
                    sentiment TEXT CHECK(sentiment IN ('positive','negative',NULL)),
                    importance_score REAL DEFAULT 0.0,
                    importance_level TEXT CHECK(importance_level IN ('high','medium','low',NULL)),
                    pub_date TEXT,
                    content_hash TEXT UNIQUE,
                    raw_content TEXT,
                    companies TEXT,
                    is_our_brand INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );

                CREATE TABLE IF NOT EXISTS sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    url_pattern TEXT,
                    source_type TEXT CHECK(source_type IN ('websearch','rss','wechat','auto_vertical','gov')),
                    authority_weight REAL DEFAULT 0.5,
                    section TEXT,
                    status TEXT DEFAULT 'active' CHECK(status IN ('active','inactive','error')),
                    last_fetched TEXT,
                    fetch_success_count INTEGER DEFAULT 0,
                    fetch_fail_count INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );

                CREATE TABLE IF NOT EXISTS daily_briefs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    brief_date TEXT UNIQUE NOT NULL,
                    sections TEXT,
                    article_count INTEGER DEFAULT 0,
                    generated_at TEXT DEFAULT (datetime('now','localtime')),
                    pushed INTEGER DEFAULT 0,
                    push_time TEXT
                );

                CREATE TABLE IF NOT EXISTS cross_refs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    article_id INTEGER NOT NULL,
                    related_article_id INTEGER NOT NULL,
                    similarity_score REAL,
                    FOREIGN KEY (article_id) REFERENCES articles(id),
                    FOREIGN KEY (related_article_id) REFERENCES articles(id)
                );

                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    article_id INTEGER NOT NULL,
                    feedback_type TEXT CHECK(feedback_type IN ('useful','irrelevant','incorrect')),
                    comment TEXT,
                    created_at TEXT DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY (article_id) REFERENCES articles(id)
                );

                CREATE INDEX IF NOT EXISTS idx_articles_section ON articles(section);
                CREATE INDEX IF NOT EXISTS idx_articles_pub_date ON articles(pub_date);
                CREATE INDEX IF NOT EXISTS idx_articles_content_hash ON articles(content_hash);
                CREATE INDEX IF NOT EXISTS idx_daily_briefs_date ON daily_briefs(brief_date);
            """)

    def content_exists(self, url: str) -> bool:
        content_hash = hashlib.md5(url.encode()).hexdigest()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM articles WHERE content_hash = ?", (content_hash,)
            ).fetchone()
            return row is not None

    def insert_article(self, article: dict) -> Optional[int]:
        content_hash = hashlib.md5(article["url"].encode()).hexdigest()
        with self._connect() as conn:
            try:
                cursor = conn.execute("""
                    INSERT OR IGNORE INTO articles
                    (title, summary, url, source_name, section, sentiment,
                     importance_score, importance_level, pub_date,
                     content_hash, raw_content, companies, is_our_brand)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    article["title"],
                    article.get("summary"),
                    article["url"],
                    article.get("source_name"),
                    article["section"],
                    article.get("sentiment"),
                    article.get("importance_score", 0.0),
                    article.get("importance_level"),
                    article.get("pub_date"),
                    content_hash,
                    article.get("raw_content"),
                    json.dumps(article.get("companies", []), ensure_ascii=False),
                    article.get("is_our_brand", 0),
                ))
                return cursor.lastrowid
            except sqlite3.IntegrityError:
                return None

    def insert_cross_ref(self, article_id: int, related_id: int, similarity: float):
        with self._connect() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO cross_refs (article_id, related_article_id, similarity_score)
                VALUES (?, ?, ?)
            """, (article_id, related_id, similarity))

    def save_daily_brief(self, brief_date: str, sections: dict, article_count: int):
        with self._connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO daily_briefs (brief_date, sections, article_count)
                VALUES (?, ?, ?)
            """, (brief_date, json.dumps(sections, ensure_ascii=False), article_count))

    def get_articles_by_date(self, target_date: str, section: str = None) -> list:
        with self._connect() as conn:
            query = "SELECT * FROM articles WHERE pub_date = ?"
            params = [target_date]
            if section:
                query += " AND section = ?"
                params.append(section)
            query += " ORDER BY importance_score DESC"
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def get_articles_by_date_range(self, start_date: str, end_date: str, section: str = None) -> list:
        with self._connect() as conn:
            query = "SELECT * FROM articles WHERE pub_date BETWEEN ? AND ?"
            params = [start_date, end_date]
            if section:
                query += " AND section = ?"
                params.append(section)
            query += " ORDER BY pub_date DESC, importance_score DESC"
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def get_brief_by_date(self, target_date: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM daily_briefs WHERE brief_date = ?", (target_date,)
            ).fetchone()
            return dict(row) if row else None

    def mark_pushed(self, brief_date: str):
        with self._connect() as conn:
            conn.execute(
                "UPDATE daily_briefs SET pushed = 1, push_time = datetime('now','localtime') WHERE brief_date = ?",
                (brief_date,)
            )

    def get_source_stats(self) -> list:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM sources ORDER BY status, name"
            ).fetchall()]

    def search_articles(self, keyword: str, section: str = None,
                        start_date: str = None, end_date: str = None, limit: int = 50) -> list:
        with self._connect() as conn:
            conditions = ["(title LIKE ? OR summary LIKE ?)"]
            params = [f"%{keyword}%", f"%{keyword}%"]
            if section:
                conditions.append("section = ?")
                params.append(section)
            if start_date:
                conditions.append("pub_date >= ?")
                params.append(start_date)
            if end_date:
                conditions.append("pub_date <= ?")
                params.append(end_date)
            query = f"SELECT * FROM articles WHERE {' AND '.join(conditions)} ORDER BY importance_score DESC LIMIT ?"
            params.append(limit)
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def get_section_stats(self, target_date: str) -> dict:
        with self._connect() as conn:
            stats = {}
            for section in ['A', 'B', 'C', 'D', 'E', 'F']:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM articles WHERE pub_date = ? AND section = ?",
                    (target_date, section)
                ).fetchone()
                stats[section] = row["cnt"]
            return stats
