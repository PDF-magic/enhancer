"""Identify a document creator and keep PDF Magic metadata consistent."""

from collections import Counter
import getpass
import os
import re
from urllib.parse import urlparse

import pikepdf


PRODUCER = "PDF Magic Enhancer"
SOURCE_NAMESPACE = "https://pdfmagic.org/ns/1.0/"
SOFTWARE = re.compile(r"ocrmypdf|tesseract|pikepdf|acrobat|adobe|microsoft|word|libreoffice|chrom(?:e|ium)|pdfkit|reportlab|scanner|ghostscript|google docs|apple pages|scribus|latex|overleaf|affinity", re.I)
CLOSING = re.compile(r"^(?:sincerely(?: yours)?|respectfully(?: submitted)?|yours (?:truly|sincerely|faithfully)|(?:best|kind|warm) regards|regards|cordially|submitted by|signed by)[,:]?$", re.I)
NOT_NAMES = set("annual report executive summary introduction conclusion confidential draft page file chapter section appendix department commission committee office securities exchange united states dear sincerely respectfully regards thank you submitted director president chief officer counsel attorney manager professor university street avenue road city county suite floor email phone fax federal preemption comments memorandum notice hearing minutes table contents".split())
PARTICLES = {"de", "del", "da", "di", "van", "von", "der", "den", "al", "bin"}


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "").replace("\u200b", "")).strip()


def person_name(value):
    name = clean_text(value)
    name = re.sub(r"^(?:/s/|signed by:|submitted by:|by:)\s*", "", name, flags=re.I)
    name = re.sub(r"^(?:Mr|Mrs|Ms|Dr|Prof)\.\s+", "", name)
    name = re.sub(r",?\s+(?:Esq\.?|Ph\.?D\.?|J\.?D\.?)$", "", name, flags=re.I)
    words = name.split()
    if not 2 <= len(words) <= 6 or len(name) > 80:
        return None
    for word in words:
        core = word.strip(".,")
        if core.casefold() in NOT_NAMES:
            return None
        if core.casefold() in PARTICLES:
            continue
        if not core or not core[0].isupper() or not all(c.isalpha() or c in "'-’." for c in core):
            return None
    return name


def text_rows(records):
    """Reassemble nearby fragments in visual page order for signature blocks."""
    rows = []
    for record in sorted(records, key=lambda item: (item["page"], -item["y"], item["x"])):
        text = clean_text(record["text"])
        if not text:
            continue
        if rows and rows[-1]["page"] == record["page"] and abs(rows[-1]["y"] - record["y"]) < 1:
            rows[-1]["text"] += " " + text
            rows[-1]["size"] = max(rows[-1]["size"], record["size"])
        else:
            rows.append({**record, "text": text})
    return rows


def visible_creator(records, pages):
    rows = text_rows(records)
    for index in range(len(rows) - 1, -1, -1):
        row = rows[index]
        if row["page"] < max(1, pages - 4):
            continue
        if re.match(r"^(?:/s/|signed by:|submitted by:)\s+", row["text"], re.I):
            name = person_name(row["text"])
            if name:
                return name, "signature"
        if CLOSING.fullmatch(row["text"]):
            for following in rows[index + 1:index + 5]:
                if following["page"] != row["page"] or row["y"] - following["y"] > 120:
                    break
                name = person_name(following["text"])
                if name:
                    return name, "signature"

    # A large first-page name can be letterhead. Exclude addressees named in
    # salutations and common titles, rather than treating any named person as author.
    recipients = {
        word.casefold().strip(".,:")
        for row in rows if row["page"] == 1 and row["text"].lower().startswith("dear ")
        for word in row["text"].split()[1:]
    }
    sizes = Counter()
    for record in records:
        if record["height"] * .1 < record["y"] < record["height"] * .8:
            sizes[round(record["size"], 1)] += len(record["text"])
    body_size = sizes.most_common(1)[0][0] if sizes else 0
    for row in rows:
        if row["page"] != 1 or row["y"] < row["height"] * .80 or row["size"] < body_size * 1.2:
            continue
        if any(word.casefold().strip(".,:") in recipients for word in row["text"].split()):
            continue
        name = person_name(row["text"])
        company = re.fullmatch(r"[\w &'’.-]{3,80}\s(?:Inc\.?|LLC|LLP|Ltd\.?|Corporation|Company)", row["text"], re.I)
        if name or company:
            return name or row["text"], "letterhead"
    return None, None


def windows_full_name(username):
    import ctypes
    from ctypes import wintypes

    class UserInfo(ctypes.Structure):
        _fields_ = [(name, wintypes.LPWSTR) for name in ("name", "comment", "user_comment", "full_name")]

    library = ctypes.WinDLL("Netapi32.dll")
    library.NetUserGetInfo.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    library.NetUserGetInfo.restype = wintypes.DWORD
    library.NetApiBufferFree.argtypes = [ctypes.c_void_p]
    buffer = ctypes.c_void_p()
    if library.NetUserGetInfo(None, username, 10, ctypes.byref(buffer)) != 0:
        return ""
    try:
        return ctypes.cast(buffer, ctypes.POINTER(UserInfo)).contents.full_name or ""
    finally:
        library.NetApiBufferFree(buffer)


def local_user_name():
    username = getpass.getuser()
    try:
        if os.name == "nt":
            full_name = windows_full_name(username)
        else:
            import pwd
            full_name = pwd.getpwuid(os.getuid()).pw_gecos.split(",", 1)[0]
            full_name = full_name.replace("&", username.capitalize())
        if clean_text(full_name):
            return clean_text(full_name)
    except (ImportError, KeyError, OSError, AttributeError):
        pass
    return username


def _first_text(value):
    if isinstance(value, (list, tuple, set)):
        values = sorted(value) if isinstance(value, set) else value
        return next((clean_text(item) for item in values if clean_text(item)), "")
    return clean_text(value)


def _keywords(value):
    if isinstance(value, (list, tuple, set)):
        values = sorted(value) if isinstance(value, set) else value
    else:
        values = re.split(r"[,;]", clean_text(value))
    seen = set()
    result = []
    for item in values:
        keyword = clean_text(item)
        if keyword and keyword.casefold() not in seen:
            seen.add(keyword.casefold())
            result.append(keyword)
    return result


def metadata_details(pdf):
    """Read reusable descriptive metadata, excluding derivative-specific IDs/dates."""
    pikepdf.models.PdfMetadata.register_xml_namespace(SOURCE_NAMESPACE, "pdfmagic")
    with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as metadata:
        authors = metadata.get("dc:creator") or []
        if isinstance(authors, str):
            authors = [authors]
        author = clean_text(pdf.docinfo.get("/Author")) or ", ".join(
            clean_text(name) for name in authors if clean_text(name)
        )
        info_creator = clean_text(pdf.docinfo.get("/Creator"))
        xmp_tool = _first_text(metadata.get("xmp:CreatorTool"))
        info_producer = clean_text(pdf.docinfo.get("/Producer"))
        xmp_producer = _first_text(metadata.get("pdf:Producer"))
        if not author and info_creator and not SOFTWARE.search(info_creator):
            author = person_name(info_creator) or ""
        keywords = metadata.get("dc:subject") or []
        if not keywords:
            keywords = metadata.get("pdf:Keywords") or pdf.docinfo.get("/Keywords") or ""
        return {
            "author": author if author and not SOFTWARE.search(author) else None,
            "creator_tool": info_creator or xmp_tool,
            "info_creator": info_creator,
            "xmp_creator_tool": xmp_tool,
            "info_producer": info_producer,
            "xmp_producer": xmp_producer,
            "title": clean_text(pdf.docinfo.get("/Title")) or _first_text(metadata.get("dc:title")),
            "description": clean_text(pdf.docinfo.get("/Subject")) or _first_text(metadata.get("dc:description")),
            "keywords": _keywords(keywords),
            "publisher": _first_text(metadata.get("dc:publisher")),
            "rights": _first_text(metadata.get("dc:rights")),
            "language": _first_text(metadata.get("dc:language")) or clean_text(pdf.Root.get("/Lang")),
            "identifier": _first_text(metadata.get("dc:identifier")),
            "href": clean_text(metadata.get("pdfmagic:href")) or clean_text(pdf.docinfo.get("/PDFMagicSourceURL")),
        }


def source_details(path):
    with pikepdf.open(path) as pdf:
        return metadata_details(pdf)


def choose_source_url(existing, supplied):
    """Keep web provenance when an enhanced local copy is processed again."""
    existing, supplied = clean_text(existing), clean_text(supplied)
    allowed = {"http", "https", "file"}
    if supplied and urlparse(supplied).scheme not in allowed:
        raise ValueError("source URL must use http, https, or file")
    if urlparse(existing).scheme not in allowed:
        existing = ""
    if existing and urlparse(existing).scheme in {"http", "https"} and urlparse(supplied).scheme == "file":
        return existing
    return supplied or existing


def enhanced_producer(original):
    """Add enhancer provenance without losing an existing PDF producer."""
    producer = clean_text(original)
    if not producer:
        return PRODUCER
    if PRODUCER.casefold() in {part.strip().casefold() for part in producer.split(";")}:
        return producer
    return f"{producer}; {PRODUCER}"


def apply_metadata(pdf, author, source_url="", original=None, overrides=None):
    """Map PDF Info and XMP without confusing the author with the creator app.

    Only write existing or explicitly supplied descriptive values. If the
    original PDF is available, prefer it over metadata inserted by OCR tools.
    """
    original = original if original is not None else metadata_details(pdf)
    overrides = overrides or {}
    author = clean_text(overrides.get("author")) or clean_text(author)
    values = {key: original.get(key) for key in (
        "title", "description", "keywords", "publisher", "rights", "language", "identifier"
    )}
    for key in values:
        value = overrides.get(key)
        if value and clean_text(value):
            values[key] = _keywords(value) if key == "keywords" else clean_text(value)

    creator_tool = clean_text(original.get("creator_tool"))
    info_creator = clean_text(original.get("info_creator")) or creator_tool
    xmp_creator_tool = clean_text(original.get("xmp_creator_tool")) or creator_tool
    info_producer = enhanced_producer(original.get("info_producer") or original.get("xmp_producer"))
    xmp_producer = enhanced_producer(original.get("xmp_producer") or original.get("info_producer"))
    keywords = _keywords(values.get("keywords") or [])
    pikepdf.models.PdfMetadata.register_xml_namespace(SOURCE_NAMESPACE, "pdfmagic")
    with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as metadata:
        if author:
            metadata["dc:creator"] = [author]
        if xmp_creator_tool:
            metadata["xmp:CreatorTool"] = xmp_creator_tool
        elif "xmp:CreatorTool" in metadata:
            del metadata["xmp:CreatorTool"]
        metadata["pdf:Producer"] = xmp_producer
        for field, prop in (
            ("title", "dc:title"),
            ("description", "dc:description"),
            ("rights", "dc:rights"),
            ("identifier", "dc:identifier"),
        ):
            if values.get(field):
                metadata[prop] = values[field]
        for field, prop in (("publisher", "dc:publisher"), ("language", "dc:language")):
            if values.get(field):
                metadata[prop] = [values[field]]
        if keywords:
            metadata["dc:subject"] = set(keywords)
            metadata["pdf:Keywords"] = ", ".join(keywords)
        if source_url:
            metadata["pdfmagic:href"] = source_url
            metadata["dc:source"] = source_url

    if author:
        pdf.docinfo["/Author"] = author
    if info_creator:
        pdf.docinfo["/Creator"] = info_creator
    elif "/Creator" in pdf.docinfo:
        del pdf.docinfo["/Creator"]
    pdf.docinfo["/Producer"] = info_producer
    for field, prop in (("title", "/Title"), ("description", "/Subject")):
        if values.get(field):
            pdf.docinfo[prop] = values[field]
    if keywords:
        pdf.docinfo["/Keywords"] = ", ".join(keywords)
    if values.get("language"):
        pdf.Root["/Lang"] = pikepdf.String(values["language"])
    if "/PDFMagicSourceURL" in pdf.docinfo:
        del pdf.docinfo["/PDFMagicSourceURL"]
