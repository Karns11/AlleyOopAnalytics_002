"""Text helpers shared by ingestion and search."""
import unicodedata


def fold(text: str) -> str:
    """Lowercase and strip accents so 'Porziņģis' matches 'porzingis'."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()
