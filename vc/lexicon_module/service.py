from __future__ import annotations

import csv
import logging
import re
import sqlite3
from pathlib import Path

from vc.config import LexiconConfig

logger = logging.getLogger(__name__)


class LexiconStore:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)

    def ensure_schema(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS terms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    term TEXT NOT NULL UNIQUE,
                    domain TEXT NOT NULL DEFAULT 'default',
                    weight INTEGER NOT NULL DEFAULT 100,
                    enabled INTEGER NOT NULL DEFAULT 1
                )
                """,
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS aliases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    term_id INTEGER NOT NULL,
                    alias TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 100,
                    UNIQUE(term_id, alias),
                    FOREIGN KEY(term_id) REFERENCES terms(id) ON DELETE CASCADE
                )
                """,
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_terms_domain ON terms(domain, enabled)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_aliases_alias ON aliases(alias)")
            conn.commit()

    def upsert_term(
        self,
        term: str,
        aliases: list[str] | tuple[str, ...],
        domain: str = "default",
        weight: int = 100,
        enabled: bool = True,
    ) -> None:
        clean_term = term.strip()
        if not clean_term:
            return
        clean_aliases = [x.strip() for x in aliases if x and x.strip()]
        if not clean_aliases:
            clean_aliases = [clean_term]
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO terms(term, domain, weight, enabled)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(term) DO UPDATE SET
                    domain=excluded.domain,
                    weight=excluded.weight,
                    enabled=excluded.enabled
                """,
                (clean_term, domain, int(weight), 1 if enabled else 0),
            )
            row = conn.execute("SELECT id FROM terms WHERE term = ?", (clean_term,)).fetchone()
            if not row:
                return
            term_id = int(row[0])
            for alias in clean_aliases:
                conn.execute(
                    """
                    INSERT INTO aliases(term_id, alias, priority)
                    VALUES(?, ?, ?)
                    ON CONFLICT(term_id, alias) DO UPDATE SET
                        priority=excluded.priority
                    """,
                    (term_id, alias, int(weight)),
                )
            conn.commit()

    def load_replacements(self, domain: str = "default") -> list[tuple[str, str, int]]:
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT a.alias, t.term, COALESCE(a.priority, t.weight, 100) AS score
                FROM aliases a
                JOIN terms t ON a.term_id = t.id
                WHERE t.enabled = 1 AND t.domain = ?
                ORDER BY LENGTH(a.alias) DESC, score DESC, a.alias ASC
                """,
                (domain,),
            ).fetchall()
        return [(str(alias), str(term), int(score)) for alias, term, score in rows]

    def list_terms(self, domain: str = "default", sort_by: str = "weight_desc") -> list[tuple[str, int, int]]:
        order_sql = "t.term ASC" if sort_by == "term_asc" else "t.weight DESC, t.term ASC"
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT t.term, t.weight, COUNT(a.id) AS alias_count
                FROM terms t
                LEFT JOIN aliases a ON a.term_id = t.id
                WHERE t.domain = ?
                GROUP BY t.id
                ORDER BY {order_sql}
                """,
                (domain,),
            ).fetchall()
        return [(str(term), int(weight), int(alias_count)) for term, weight, alias_count in rows]

    def export_rows(self, domain: str = "default") -> list[dict[str, str | int]]:
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT t.term, t.domain, t.weight, GROUP_CONCAT(a.alias, ',') AS aliases
                FROM terms t
                LEFT JOIN aliases a ON a.term_id = t.id
                WHERE t.domain = ?
                GROUP BY t.id
                ORDER BY t.weight DESC, t.term ASC
                """,
                (domain,),
            ).fetchall()
        out: list[dict[str, str | int]] = []
        for term, term_domain, weight, aliases in rows:
            out.append({"term": str(term), "aliases": str(aliases or ""), "domain": str(term_domain), "weight": int(weight)})
        return out

    def import_csv(self, csv_path: str | Path, fallback_domain: str = "default") -> dict[str, int]:
        path = Path(csv_path)
        if not path.is_file():
            raise FileNotFoundError(f"CSV 不存在: {path}")
        self.ensure_schema()
        report = {"total": 0, "imported": 0, "skipped": 0, "failed": 0}
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                report["total"] += 1
                try:
                    term = str(row.get("term") or "").strip()
                    if not term:
                        report["skipped"] += 1
                        continue
                    aliases_raw = str(row.get("aliases") or "").strip()
                    aliases = [x.strip() for x in aliases_raw.split(",") if x.strip()]
                    domain = str(row.get("domain") or fallback_domain).strip() or fallback_domain
                    weight_raw = str(row.get("weight") or "100").strip()
                    try:
                        weight = int(weight_raw)
                    except ValueError:
                        report["skipped"] += 1
                        continue
                    self.upsert_term(term=term, aliases=aliases, domain=domain, weight=weight)
                    report["imported"] += 1
                except Exception:
                    report["failed"] += 1
                    logger.debug("导入行失败: %s", row, exc_info=True)
        return report

    def delete_term(self, term: str, domain: str = "default") -> bool:
        clean_term = term.strip()
        if not clean_term:
            return False
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute("SELECT id FROM terms WHERE term = ? AND domain = ?", (clean_term, domain)).fetchone()
            if not row:
                return False
            term_id = int(row[0])
            conn.execute("DELETE FROM aliases WHERE term_id = ?", (term_id,))
            conn.execute("DELETE FROM terms WHERE id = ?", (term_id,))
            conn.commit()
        return True

    def get_aliases(self, term: str, domain: str = "default") -> list[str]:
        clean_term = term.strip()
        if not clean_term:
            return []
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT a.alias
                FROM aliases a
                JOIN terms t ON a.term_id = t.id
                WHERE t.term = ? AND t.domain = ?
                ORDER BY a.alias ASC
                """,
                (clean_term, domain),
            ).fetchall()
        return [str(alias) for (alias,) in rows]

    def replace_term_aliases(
        self,
        term: str,
        aliases: list[str] | tuple[str, ...],
        domain: str = "default",
        weight: int = 100,
        enabled: bool = True,
    ) -> None:
        clean_term = term.strip()
        if not clean_term:
            return
        clean_aliases = [x.strip() for x in aliases if x and x.strip()]
        if not clean_aliases:
            clean_aliases = [clean_term]
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO terms(term, domain, weight, enabled)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(term) DO UPDATE SET
                    domain=excluded.domain,
                    weight=excluded.weight,
                    enabled=excluded.enabled
                """,
                (clean_term, domain, int(weight), 1 if enabled else 0),
            )
            row = conn.execute("SELECT id FROM terms WHERE term = ?", (clean_term,)).fetchone()
            if not row:
                return
            term_id = int(row[0])
            conn.execute("DELETE FROM aliases WHERE term_id = ?", (term_id,))
            for alias in clean_aliases:
                conn.execute("INSERT INTO aliases(term_id, alias, priority) VALUES(?, ?, ?)", (term_id, alias, int(weight)))
            conn.commit()


class LexiconCorrector:
    def __init__(self, cfg: LexiconConfig) -> None:
        self._cfg = cfg
        self._store = LexiconStore(cfg.db_path)
        if cfg.enabled:
            self._store.ensure_schema()

    @property
    def enabled(self) -> bool:
        return self._cfg.enabled

    def correct(self, text: str) -> tuple[str, int]:
        if not self._cfg.enabled or not text.strip():
            return text, 0
        try:
            replacements = self._store.load_replacements(domain=self._cfg.domain)
        except Exception:
            logger.exception("词库读取失败，跳过本轮纠正")
            return text, 0
        if not replacements:
            return text, 0
        alias_to_term = {alias: term for alias, term, _ in replacements if alias}
        if not alias_to_term:
            return text, 0
        ordered_aliases = sorted(alias_to_term.keys(), key=len, reverse=True)
        pattern = re.compile("|".join(re.escape(alias) for alias in ordered_aliases))
        count = 0

        def _replace(m: re.Match[str]) -> str:
            nonlocal count
            source = m.group(0)
            target = alias_to_term.get(source, source)
            if target != source:
                count += 1
            return target

        corrected = pattern.sub(_replace, text)
        return corrected, count
