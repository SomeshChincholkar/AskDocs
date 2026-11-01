from app.services.chunking import chunk_pages, split_text


def test_split_text_respects_size_and_overlap():
    text = " ".join(f"word{i}" for i in range(100))
    chunks = split_text(text, chunk_size=80, overlap=12)

    assert len(chunks) > 1
    assert all(0 < len(chunk) <= 80 for chunk in chunks)
    assert all(chunks[index][-12:] in chunks[index + 1] for index in range(len(chunks) - 1))


def test_chunk_pages_keeps_page_numbers_and_skips_empty_text():
    chunks = chunk_pages([(2, "alpha " * 30), (3, "   ")], chunk_size=30, overlap=5)

    assert chunks
    assert all(page == 2 for page, _ in chunks)
    assert all(content.strip() for _, content in chunks)


def test_split_text_rejects_invalid_overlap():
    try:
        split_text("text", chunk_size=10, overlap=10)
    except ValueError as error:
        assert "overlap" in str(error)
    else:
        raise AssertionError("Invalid overlap should raise ValueError")
