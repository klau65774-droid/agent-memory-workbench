"""The read surface that decides disclosure itself, instead of delegating it to the host."""

from agent_memory.core.context import NOTHING_FOUND, build, build_progressive, estimate_tokens
from agent_memory.core.recall import Recall


def test_context_is_deterministic_for_the_same_store_and_question(seeded):
    first = build(seeded, "deploy drain queue")
    second = build(seeded, "deploy drain queue")
    assert first.text == second.text
    assert first.entries == second.entries


def test_the_top_entries_arrive_as_full_text_and_the_tail_as_abstracts(seeded):
    query = "deploy drain queue file truth concise answers"
    wide = build(seeded, query)
    seeded.config.recall.context_full_text_entries = 1
    narrow = build(seeded, query)

    assert wide.entries == narrow.entries > 1
    assert len(narrow.text) < len(wide.text)
    opened = seeded.read(narrow.names[0]).text
    assert opened.strip() and opened.strip() in narrow.text


def test_an_empty_store_says_so_rather_than_pretending(store):
    context = build(store, "anything at all")
    assert context.is_empty()
    assert context.text


def test_context_carries_what_recall_carries(seeded):
    hits = Recall(seeded).recall("deploy drain queue")
    context = build(seeded, "deploy drain queue")
    assert context.entries == len(hits)
    assert hits[0].abstract in context.text


def test_reads_behind_the_context_do_not_mutate_truth(seeded):
    import hashlib

    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in seeded.layout.truth_files()}
    build(seeded, "deploy drain queue")
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in seeded.layout.truth_files()}
    assert after == before


def test_context_only_reaches_memory(store):
    store.record(
        abstract="Watches nature documentaries",
        type="preference",
        name="nature-documentaries",
    )
    store.archive.append_session(
        "s1", "user: the follow-up you named was 'My Octopus Teacher' and it cost 42 dollars\n"
    )
    store.sync_index()
    assert "Octopus" not in build(store, "My Octopus Teacher").text


def test_empty_progressive_and_full_renderings_have_identical_cost(store):
    preview, budget = build_progressive(store, [], [])
    assert preview.text == NOTHING_FOUND
    assert budget.full_store_tokens == budget.progressive_context_tokens
    assert budget.progressive_context_tokens == estimate_tokens(preview.text)
    assert budget.tokens_saved == budget.savings_pct == 0
