from src.retrieve.retriever import _chunk_document


def test_chunk_document_splits_on_headings():
    doc = {
        "doc_id": "DOC-X",
        "content": "# Title\n\n## Symptoms\nSomething is broken.\n\n## Resolution\n1. Step one.\n2. Step two.",
    }
    chunks = _chunk_document(doc)
    assert len(chunks) >= 2
    assert any("Step one" in c and "Step two" in c for c in chunks)  # numbered sequence stays intact


def test_chunk_document_handles_empty_content():
    assert _chunk_document({"doc_id": "DOC-EMPTY", "content": ""}) == []


def test_chunk_document_handles_missing_content_key():
    assert _chunk_document({"doc_id": "DOC-NOCONTENT"}) == []


def test_chunk_document_falls_back_to_whole_doc_when_no_headings():
    doc = {"doc_id": "DOC-FLAT", "content": "Just a plain paragraph with no markdown headings at all."}
    chunks = _chunk_document(doc)
    assert len(chunks) == 1
