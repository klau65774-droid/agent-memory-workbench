"""CJK tokenization: query extraction, index-side segmentation, and drift rebuild."""

import pytest
from agent_memory.core.access_log import AccessLog
from agent_memory.core.config import Config
from agent_memory.core.database import Database
from agent_memory.core.manifest import IndexMeta
from agent_memory.core.recall import Recall
from agent_memory.core.search_index import SearchIndex, to_match_query
from agent_memory.core.store import Store
from agent_memory.core.tokenization import (
    TOKENIZER_CJK,
    TOKENIZER_HYBRID,
    query_tokens,
    segment_text,
)


def test_ascii_queries_keep_the_current_expression():
    assert to_match_query("deploy fails E4021") == '"deploy" OR "fails" OR "e4021"'


def test_legacy_ascii_and_opt_in_hybrid_keep_separate_query_vocabularies():
    assert to_match_query("构件命名规范") == ""
    tokens = query_tokens("构件命名规范", TOKENIZER_HYBRID, use_jieba=False)
    assert tokens == ["构", "构件", "件命", "命名", "名规", "规范", "范"]
    assert '"命名"' in to_match_query("构件命名规范", TOKENIZER_HYBRID)


def test_unrelated_cjk_queries_do_not_match_english_only_memories(seeded):
    assert Recall(seeded).recall("队列排空时部署失败") == []


def test_unknown_tokenizer_is_rejected(store):
    store.config.index.tokenizer = "soundex"
    with pytest.raises(ValueError, match="unknown tokenizer"):
        store.record(
            abstract="any abstract",
            type="fact",
            name="bad-tokenizer",
        )


def test_missing_jieba_falls_back_and_strict_mode_has_an_install_hint(store, monkeypatch):
    import builtins
    import sys

    store.config.index.tokenizer = TOKENIZER_HYBRID
    monkeypatch.delitem(sys.modules, "jieba", raising=False)
    real_import = builtins.__import__

    def no_jieba(name, *args, **kwargs):
        if name == "jieba":
            raise ImportError("no jieba")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_jieba)
    store.record(
        abstract="构件命名规范",
        type="decision",
        body="数字孪生项目的构件命名采用 wc_类_L层-序号 格式。",
        name="naming-convention",
    )
    assert Recall(store).recall("构件命名")[0].name == "naming-convention"
    with pytest.raises(RuntimeError, match="agent-memory-core\\[cjk\\]"):
        segment_text("构件命名规范", TOKENIZER_HYBRID, strict_jieba=True)
    with pytest.raises(RuntimeError, match="agent-memory-core\\[cjk\\]"):
        query_tokens("构件命名规范", TOKENIZER_CJK)


@pytest.fixture
def cjk_store(tmp_path, clock):
    pytest.importorskip("jieba")
    config = Config.default()
    config.index.tokenizer = TOKENIZER_CJK
    store = Store(tmp_path / "store", config=config, clock=clock, agent="test-agent")
    store.init()
    return store


@pytest.fixture
def cjk_seeded(cjk_store):
    cjk_store.record(
        abstract="构件命名采用 wc_类_L层-序号 格式",
        type="decision",
        body="数字孪生项目的构件命名采用 wc_wall_L3-012 形式，由 JSON 事实源流水线强制执行。",
        name="naming-convention",
    )
    cjk_store.record(
        abstract="Blender 自动化必须走 bpy headless CLI",
        type="fact",
        body="Blender 的 GUI 自动化不可行，批量渲染必须通过 bpy 的 headless 命令行接口执行。",
        name="blender-headless",
    )
    cjk_store.record(
        abstract="平面图识别采用渐进式策略",
        type="procedure",
        body="PDF 转结构化数据时先低分辨率整体定位，再对构件区域高精度识别，避免内存溢出。",
        name="progressive-recognition",
    )
    return cjk_store


def test_hybrid_query_tokens_include_words_and_edge_characters():
    tokens = query_tokens("构件命名规范是什么", TOKENIZER_HYBRID)
    assert "构件" in tokens and "命名" in tokens
    assert "构" in tokens and "么" in tokens


def test_installed_jieba_enriches_the_builtin_tokens():
    pytest.importorskip("jieba")
    text = "中华人民共和国"
    builtin = query_tokens(text, TOKENIZER_HYBRID, use_jieba=False)
    enriched = query_tokens(text, TOKENIZER_HYBRID)
    assert set(builtin) < set(enriched)
    assert text in enriched


def test_explicit_dictionary_settings_change_the_recall_fingerprint():
    config = Config.default()
    original = config.recall_fingerprint()
    config.index.use_jieba = False
    assert config.recall_fingerprint() != original
    config.index.use_jieba = True
    config.index.strict_jieba = True
    assert config.recall_fingerprint() != original


def test_ascii_literals_survive_cjk_segmentation():
    pytest.importorskip("jieba")
    segmented = segment_text("Blender 的 bpy headless CLI", TOKENIZER_CJK)
    assert "Blender" in segmented.split() and "headless" in segmented.split()


def test_pure_cjk_query_retrieves_the_matching_memory(cjk_seeded):
    hits = Recall(cjk_seeded).recall("构件命名规范是什么")
    assert hits[0].name == "naming-convention"


def test_mixed_query_still_matches_ascii_literals(cjk_seeded):
    hits = Recall(cjk_seeded).recall("blender 怎么自动化")
    assert "blender-headless" in [hit.name for hit in hits]


def test_cjk_heading_stays_readable(cjk_seeded):
    cjk_seeded.record(
        abstract="带标题的中文记忆",
        type="fact",
        body="# 构件命名规范\n正文必须包含足够的字符数以触发分节切块。" * 8,
        name="with-heading",
    )
    hits = [hit for hit in Recall(cjk_seeded).recall("命名") if hit.name == "with-heading"]
    if hits:
        assert hits[0].heading in ("", "构件命名规范")


def test_switching_tokenizer_rebuilds_the_projection(tmp_path, clock):
    config = Config.default()
    store = Store(tmp_path / "store", config=config, clock=clock, agent="test-agent")
    store.init()
    store.record(
        abstract="构件命名采用 wc 格式",
        type="decision",
        body="数字孪生项目的构件命名规范。",
        name="naming-convention",
    )
    assert Recall(store).recall("构件命名") == []
    with Database(store.layout).connect() as connection:
        original = IndexMeta(connection).tokenizer()

    config.index.tokenizer = TOKENIZER_HYBRID
    store.record(  # any later write re-syncs, and drift forces a full rebuild first
        abstract="another memory",
        type="fact",
        body="an unrelated english body.",
        name="another",
    )
    hits = Recall(store).recall("构件命名")
    assert "naming-convention" in [hit.name for hit in hits]
    with Database(store.layout).connect() as connection:
        assert IndexMeta(connection).tokenizer() != original


def test_enabling_cjk_rebuilds_a_legacy_projection_without_tokenizer_metadata(
    tmp_path, clock
):
    config = Config.default()
    store = Store(tmp_path / "store", config=config, clock=clock, agent="test-agent")
    store.init()
    store.record(
        abstract="构件命名采用 wc 格式",
        type="decision",
        body="数字孪生项目的构件命名规范。",
        name="naming-convention",
    )
    with Database(store.layout).connect() as connection:
        connection.execute("DELETE FROM index_meta")

    config.index.tokenizer = TOKENIZER_HYBRID
    store.record(
        abstract="another memory",
        type="fact",
        body="an unrelated english body.",
        name="another",
    )

    hits = Recall(store).recall("构件命名")
    assert "naming-convention" in [hit.name for hit in hits]


def test_first_recall_updates_legacy_ascii_projection_and_keeps_the_access_log(store):
    record = store.record(name="naming-policy", type="fact", abstract="构件命名规范")
    store.read(record.name)
    with Database(store.layout).connect() as connection:
        IndexMeta(connection).set_tokenizer("ascii")
        connection.execute("UPDATE chunks SET text = ?", (record.abstract,))
    store.config.index.tokenizer = TOKENIZER_HYBRID
    reopened = Store(store.root, config=store.config, clock=store.clock)
    assert Recall(reopened).recall("命名", log=False)[0].name == record.name
    with Database(reopened.layout).connect() as connection:
        assert AccessLog(connection).counts() == {record.name: 1}


def test_hybrid_retrieves_a_chinese_heading_and_its_edge_and_interior_unigrams(store):
    store.config.index.tokenizer = TOKENIZER_HYBRID
    store.config.index.use_jieba = False
    store.record(
        name="tier-document", type="fact", abstract="Enterprise tier routing policy",
        body="## 铂金响应时限\n" + "Notes for reviewing the tier routing policy.\n" * 8,
    )
    for query in ("响应时限", "铂", "应"):
        hit = Recall(store).recall(query)[0]
        assert hit.name == "tier-document"
        assert hit.heading == "铂金响应时限"


def test_first_query_restores_a_missing_projection_from_markdown(seeded):
    expected = Recall(seeded).recall("deploy", log=False)
    seeded.layout.index_db.unlink()
    assert Recall(seeded).recall("deploy", log=False) == expected


def test_reader_keeps_its_projected_vocabulary_during_another_clients_rebuild(store):
    store.config.index.tokenizer = TOKENIZER_HYBRID
    store.config.index.use_jieba = False
    store.record(name="handover", type="fact", abstract="服务容灾演练")
    legacy = Store(store.root, config=Config.default(), clock=store.clock)
    with Database(store.layout).connect() as connection:
        connection.execute("PRAGMA journal_mode=WAL")

    with store.search_connection(log=False) as (snapshot, tokenizer):
        legacy.rebuild_index()
        assert IndexMeta(snapshot).tokenizer() == tokenizer.signature
        assert SearchIndex(snapshot, tokenizer).match("容灾", 3)[0].name == "handover"
    assert Recall(legacy).recall("容灾", log=False) == []
    assert Recall(store).recall("容灾", log=False)[0].name == "handover"


def test_version_changes_reproject_both_surfaces_and_keep_usage(store, monkeypatch):
    from agent_memory.core import tokenization

    store.config.index.tokenizer = TOKENIZER_HYBRID
    store.config.index.use_jieba = False
    store.record(name="before", type="fact", abstract="旧的压缩约定", valid_from="2025-01-01")
    store.record(
        name="after", type="fact", abstract="新的压缩约定", valid_from="2025-06-01",
        supersedes="before",
    )
    store.read("after")
    with Database(store.layout).connect() as connection:
        for surface in ("chunks", "history"):
            connection.execute(f"UPDATE {surface} SET text = 'stale'")
    monkeypatch.setattr(tokenization, "_PROJECTION_VERSION", tokenization._PROJECTION_VERSION + 1)
    assert Recall(store).recall("压缩", log=False)[0].name == "after"
    assert Recall(store).recall("压缩", as_of="2025-05-01", log=False)[0].name == "before"
    store.rebuild_index()
    with Database(store.layout).connect() as connection:
        assert AccessLog(connection).counts() == {"after": 1}
