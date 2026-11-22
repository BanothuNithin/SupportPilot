import pytest

from app.rag.chunking import chunk_text


def test_chunk_empty_text_returns_empty_list():
    assert chunk_text("") == []
    assert chunk_text(" \n\r\n\t ") == []


def test_chunks_never_exceed_size():
    text = " ".join(f"word{i}" for i in range(200))
    chunks = chunk_text(text, size=80, overlap=10)

    assert chunks
    assert all(len(chunk) <= 80 for chunk in chunks)


def test_overlap_repeats_text_between_chunks():
    text = (
        "Alpha beta gamma delta epsilon.\n\n"
        "Zeta eta theta iota kappa lambda.\n\n"
        "Mu nu xi omicron pi rho sigma."
    )
    chunks = chunk_text(text, size=55, overlap=12)

    assert len(chunks) >= 2
    repeated = False
    for left, right in zip(chunks, chunks[1:]):
        overlap_length = max(
            length
            for length in range(1, min(len(left), len(right)) + 1)
            if left[-length:] == right[:length]
        )
        if overlap_length >= 4:
            repeated = True
            break
    assert repeated


def test_overlap_must_be_smaller_than_size():
    with pytest.raises(ValueError, match="overlap must be smaller than size"):
        chunk_text("some text", size=10, overlap=10)
