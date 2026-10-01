"""CIK normalisation shared by the SEC filing parsers."""


def ten_digit_cik(raw: str | None) -> str | None:
    """A 10-digit CIK from ASCII digits only; anything else (letters, unicode digits, more
    than ten digits, empty) is unknown rather than a padded string the tables would reject."""
    if raw is None:
        return None
    value = raw.strip()
    if not value or not (value.isascii() and value.isdigit()) or len(value) > 10:
        return None
    return value.zfill(10)
