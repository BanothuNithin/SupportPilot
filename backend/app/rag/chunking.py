import re


def _split_long(paragraph: str, size: int) -> list[str]:
    """Split one over-long paragraph on sentence boundaries; hard-split sentences that are still too long."""
    out: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
        while len(sentence) > size:  # a single huge sentence: cut at word boundaries
            cut = sentence.rfind(" ", 0, size)
            cut = cut if cut > 0 else size
            if current:
                out.append(current)
                current = ""
            out.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if not sentence:
            continue
        if current and len(current) + 1 + len(sentence) > size:
            out.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        out.append(current)
    return out


def chunk_text(text: str, size: int = 800, overlap: int = 100) -> list[str]:
    """Paragraph-aware chunking. Every chunk is at most `size` characters.

    Paragraphs are packed together until `size` is reached. Each new chunk starts with the tail of the
    previous one (`overlap` characters) so an idea cut at a boundary still appears whole in one chunk.
    """
    if overlap >= size:
        raise ValueError("overlap must be smaller than size")
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []

    pieces: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        pieces.extend([para] if len(para) <= size else _split_long(para, size))

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if not current:
            current = piece
        elif len(current) + 2 + len(piece) <= size:
            current += "\n\n" + piece
        else:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            if " " in tail:
                tail = tail[tail.index(" ") + 1:]  # do not start mid-word
            candidate = f"{tail}\n\n{piece}" if tail else piece
            current = candidate if len(candidate) <= size else piece
    if current:
        chunks.append(current)
    return chunks
