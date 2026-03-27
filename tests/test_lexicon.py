from __future__ import annotations

from pathlib import Path

from vc.config import LexiconConfig
from vc.lexicon_module.service import LexiconCorrector, LexiconStore


def test_lexicon_store_upsert_and_load(tmp_path: Path) -> None:
    db_path = tmp_path / "lexicon.db"
    store = LexiconStore(db_path)
    store.ensure_schema()
    store.upsert_term("LangChain", aliases=["郎圈", "蓝链"], domain="default", weight=120)

    rows = store.load_replacements("default")
    assert ("郎圈", "LangChain", 120) in rows
    assert ("蓝链", "LangChain", 120) in rows


def test_lexicon_corrector_replaces_terms(tmp_path: Path) -> None:
    db_path = tmp_path / "lexicon.db"
    store = LexiconStore(db_path)
    store.ensure_schema()
    store.upsert_term("LangChain", aliases=["郎圈"], domain="default")
    store.upsert_term("SQLite", aliases=["思酷来特"], domain="default")

    corrector = LexiconCorrector(
        LexiconConfig(enabled=True, db_path=str(db_path), domain="default"),
    )
    corrected, hit = corrector.correct("我准备用郎圈和思酷来特做一个demo")
    assert corrected == "我准备用LangChain和SQLite做一个demo"
    assert hit == 2


def test_lexicon_corrector_disabled_passthrough(tmp_path: Path) -> None:
    db_path = tmp_path / "lexicon.db"
    corrector = LexiconCorrector(
        LexiconConfig(enabled=False, db_path=str(db_path), domain="default"),
    )
    text, hit = corrector.correct("郎圈")
    assert text == "郎圈"
    assert hit == 0


def test_lexicon_store_list_terms(tmp_path: Path) -> None:
    db_path = tmp_path / "lexicon.db"
    store = LexiconStore(db_path)
    store.ensure_schema()
    store.upsert_term("LangChain", aliases=["郎圈", "朗链"], domain="default", weight=120)
    store.upsert_term("SQLite", aliases=["思酷来特"], domain="default", weight=110)

    rows = store.list_terms("default")
    assert rows[0][0] == "LangChain"
    assert rows[0][1] == 120
    assert rows[0][2] == 2


def test_lexicon_store_delete_term(tmp_path: Path) -> None:
    db_path = tmp_path / "lexicon.db"
    store = LexiconStore(db_path)
    store.ensure_schema()
    store.upsert_term("LangChain", aliases=["郎圈"], domain="default", weight=120)

    assert store.delete_term("LangChain", domain="default") is True
    rows = store.list_terms("default")
    assert rows == []
    assert store.delete_term("LangChain", domain="default") is False


def test_lexicon_store_replace_aliases_and_get_aliases(tmp_path: Path) -> None:
    db_path = tmp_path / "lexicon.db"
    store = LexiconStore(db_path)
    store.ensure_schema()
    store.upsert_term("LangChain", aliases=["郎圈", "朗链"], domain="default", weight=120)

    store.replace_term_aliases("LangChain", aliases=["蓝链"], domain="default", weight=120)
    aliases = store.get_aliases("LangChain", domain="default")
    assert aliases == ["蓝链"]


def test_lexicon_store_list_terms_sort_by_term(tmp_path: Path) -> None:
    db_path = tmp_path / "lexicon.db"
    store = LexiconStore(db_path)
    store.ensure_schema()
    store.upsert_term("Zoo", aliases=["zoo"], domain="default", weight=999)
    store.upsert_term("Alpha", aliases=["alpha"], domain="default", weight=1)

    rows = store.list_terms("default", sort_by="term_asc")
    assert rows[0][0] == "Alpha"
    assert rows[1][0] == "Zoo"


def test_lexicon_store_export_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "lexicon.db"
    store = LexiconStore(db_path)
    store.ensure_schema()
    store.upsert_term("LangChain", aliases=["郎圈", "朗链"], domain="default", weight=120)

    rows = store.export_rows("default")
    assert len(rows) == 1
    assert rows[0]["term"] == "LangChain"
    assert rows[0]["domain"] == "default"
    assert rows[0]["weight"] == 120
    assert "郎圈" in str(rows[0]["aliases"])


def test_lexicon_store_import_csv_report(tmp_path: Path) -> None:
    db_path = tmp_path / "lexicon.db"
    csv_path = tmp_path / "lexicon.csv"
    csv_path.write_text(
        "term,aliases,domain,weight\n"
        "LangChain,郎圈|朗链,default,120\n"
        ",无效,default,100\n"
        "SQLite,思酷来特,default,bad\n",
        encoding="utf-8",
    )

    store = LexiconStore(db_path)
    report = store.import_csv(csv_path=csv_path, fallback_domain="default")
    assert report["total"] == 3
    assert report["imported"] == 1
    assert report["skipped"] == 2
    assert report["failed"] == 0
