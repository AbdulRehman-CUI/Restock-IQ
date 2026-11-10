"""Bounded quote-only uploads: parse, preview, explicitly confirm, atomically append."""
import csv
import io
import re
from datetime import timedelta
from pathlib import PurePath
from uuid import uuid4
from zipfile import ZipFile, BadZipFile
from xml.etree import ElementTree as ET

from pydantic import ValidationError
from sqlalchemy import select, delete
from app.api.schemas.market import QuoteInput
from app.core.errors import NotFound, StateConflict
from app.db.market import QuoteUpload, QuoteChunk
from app.services.fx import instant, now_utc
from app.services.market import dataset_record, validate_quote_product, insert_quote, digest

CHUNK_BYTES = 512 * 1024
MAX_ROWS = 1000
MAX_EXPANSION = 8 * 1024 * 1024
FIELDS = [name for name in QuoteInput.model_fields if name != "source_type"]
NULLABLE = {"valid_until", "max_quantity", "freight_unit_pkr", "other_unit_pkr"}


def template_csv():
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(FIELDS)
    return output.getvalue()


def xml(payload):
    if b"\x00" in payload or b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        raise ValueError("XML entities, document types and non-UTF-8 XML are unsupported")
    return ET.fromstring(payload)


def xlsx_rows(payload):
    try:
        with ZipFile(io.BytesIO(payload)) as book:
            entries = book.infolist()
            names = [item.filename for item in entries]
            if len(entries) > 100 or len(set(names)) != len(names) or sum(item.file_size for item in entries) > MAX_EXPANSION:
                raise ValueError("Spreadsheet expansion exceeds 8 MiB or has excessive/duplicate parts")
            if any(item.flag_bits & 1 or item.compress_type not in (0, 8) for item in entries):
                raise ValueError("Encrypted or unsupported spreadsheet compression")
            if any("vbaproject" in n.lower() or "externallinks" in n.lower() or "macrosheet" in n.lower() for n in names):
                raise ValueError("Macros and external links are unsupported")
            parts = {}
            for item in entries:
                with book.open(item) as stream:
                    content = stream.read(MAX_EXPANSION + 1)
                    if len(content) > MAX_EXPANSION:
                        raise ValueError("Spreadsheet part exceeds expansion limit")
                    parts[item.filename] = content
            for name, content in parts.items():
                if name.endswith(".rels"):
                    relationships = xml(content)
                    if any(r.get("TargetMode", "").lower() == "external" for r in relationships):
                        raise ValueError("External spreadsheet relationships are unsupported")
            if "[Content_Types].xml" not in parts or b"macroEnabled" in parts["[Content_Types].xml"]:
                raise ValueError("Expected non-macro XLSX workbook")
            sheets = [n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)]
            if len(sheets) != 1:
                raise ValueError("Use one worksheet containing the documented template")
            ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            shared = []
            if "xl/sharedStrings.xml" in parts:
                shared = ["".join(node.itertext()) for node in xml(parts["xl/sharedStrings.xml"]).findall("s:si", ns)]
                if len(shared) > 20000:
                    raise ValueError("Too many shared strings")
            sheet = xml(parts[sheets[0]])
            if sheet.findall(".//s:f", ns):
                raise ValueError("Formulas are unsupported; paste literal values, including ISO timestamps")
            rows = []
            for row in sheet.findall("s:sheetData/s:row", ns):
                if len(rows) > MAX_ROWS or int(row.get("r", "0")) > MAX_ROWS + 1:
                    raise ValueError("At most 1000 quote rows are supported")
                cells = {}
                for cell in row.findall("s:c", ns):
                    ref = cell.get("r", "")
                    match = re.fullmatch(r"([A-Z]{1,2})([1-9][0-9]{0,6})", ref)
                    if not match or int(match[2]) > MAX_ROWS + 1:
                        raise ValueError("Invalid or excessive cell address")
                    col = 0
                    for char in match[1]:
                        col = col * 26 + ord(char) - 64
                    if col > len(FIELDS) or col in cells:
                        raise ValueError("Too many or duplicate spreadsheet columns")
                    kind = cell.get("t")
                    value = cell.findtext("s:v", "", ns)
                    if kind == "s":
                        index = int(value)
                        if not 0 <= index < len(shared):
                            raise ValueError("Invalid shared string index")
                        value = shared[index]
                    elif kind == "inlineStr":
                        value = "".join(node.text or "" for node in cell.findall("s:is//s:t", ns))
                    elif kind not in (None, "n", "str"):
                        raise ValueError("Use literal text/numeric cells; timestamps must be ISO text")
                    if len(value) > 4096:
                        raise ValueError("Cell exceeds 4096 characters")
                    cells[col] = value
                rows.append([cells.get(i, "") for i in range(1, max(cells, default=0) + 1)])
            return rows
    except (BadZipFile, ET.ParseError, KeyError, IndexError, OverflowError) as exc:
        raise ValueError("Malformed or unsupported XLSX workbook") from exc


def parse_quotes(payload, filename, session, dataset_id):
    extension = PurePath(filename).suffix.lower()
    if extension == ".xlsx":
        rows = xlsx_rows(payload)
    elif extension == ".csv":
        try:
            rows = []
            for row in csv.reader(io.StringIO(payload.decode("utf-8-sig")), strict=True):
                if len(rows) > MAX_ROWS:
                    raise ValueError("At most 1000 quote rows are supported")
                if len(row) > len(FIELDS) or any(len(value) > 4096 for value in row):
                    raise ValueError("Excessive columns or cell length")
                rows.append(row)
        except (UnicodeError, csv.Error) as exc:
            raise ValueError("Expected UTF-8 CSV matching the quote template") from exc
    else:
        raise ValueError("Only CSV and non-macro XLSX are supported")
    if not rows or rows[0] != FIELDS:
        raise ValueError("Header must match the downloadable quote template, in the documented order")
    findings, quotes, seen = [], [], set()
    for line, row in enumerate(rows[1:], 2):
        if not any(row):
            continue
        try:
            if len(row) != len(FIELDS):
                raise ValueError("Row has missing/extra columns")
            values = {key: (None if not value.strip() and key in NULLABLE else value.strip())
                      for key, value in zip(FIELDS, row)}
            values["source_type"] = "XLSX" if extension == ".xlsx" else "CSV"
            quote = QuoteInput.model_validate(values)
            validate_quote_product(session, dataset_id, quote)
            # One version per SKU/observation/tier; conflicting duplicates fail the entire import.
            key = (quote.sku, instant(quote.observed_at), quote.min_quantity, quote.max_quantity)
            if key in seen:
                raise ValueError("Duplicate SKU/observation/quantity-tier entry")
            seen.add(key)
            quotes.append(quote.model_dump(mode="json"))
        except (ValidationError, ValueError, StateConflict) as exc:
            findings.append({"row": line, "message": str(exc)[:1200]})
    if not quotes and not findings:
        findings.append({"row": None, "message": "Enter at least one quote below the header"})
    return {"quotes": quotes, "findings": findings[:100], "error_count": len(findings), "row_count": len(quotes),
            "can_confirm": bool(quotes) and not findings}


def cleanup_uploads(db, now=None):
    now = now or now_utc()
    with db.transaction() as session:
        expired = select(QuoteUpload.id).where(QuoteUpload.expires_at < instant(now))
        session.execute(delete(QuoteChunk).where(QuoteChunk.upload_id.in_(expired)))
        session.execute(delete(QuoteUpload).where(QuoteUpload.expires_at < instant(now), QuoteUpload.status != "completed"))
        session.execute(delete(QuoteUpload).where(QuoteUpload.created_at < instant(now - timedelta(days=7))))


def start_upload(db, dataset_id, body, max_bytes):
    if PurePath(body.filename).suffix.lower() not in (".csv", ".xlsx"):
        raise StateConflict("Only .csv and non-macro .xlsx files are supported")
    if body.size > max_bytes:
        raise StateConflict("Quote file exceeds configured upload limit")
    cleanup_uploads(db)
    now = now_utc()
    with db.transaction() as session:
        dataset_record(session, dataset_id)
        row = QuoteUpload(id=str(uuid4()), dataset_id=str(dataset_id), filename=body.filename,
            expected_bytes=body.size, total_bytes=0, created_at=instant(now), expires_at=instant(now + timedelta(days=1)), status="open")
        session.add(row)
        return {"id": row.id, "chunk_bytes": CHUNK_BYTES, "expires_at": row.expires_at}


def upload_record(session, identifier):
    row = session.scalar(select(QuoteUpload).where(QuoteUpload.id == str(identifier)).with_for_update())
    if row is None:
        raise NotFound("Quote upload not found or expired")
    if row.expires_at < instant(now_utc()) and row.status != "completed":
        raise StateConflict("Quote upload expired; start a new upload")
    return row


def put_chunk(db, identifier, position, data):
    if not 0 <= position < 24 or not 0 < len(data) <= CHUNK_BYTES:
        raise StateConflict("Use nonempty chunks of at most 512 KiB and positions 0–23")
    with db.transaction() as session:
        row = upload_record(session, identifier)
        if row.status != "open":
            raise StateConflict("Quote upload already previewed or committed")
        old = session.get(QuoteChunk, (str(identifier), position))
        if old:
            if old.payload != data:
                raise StateConflict("Chunk position already contains different data")
        else:
            if row.total_bytes + len(data) > row.expected_bytes:
                raise StateConflict("Chunks exceed the declared file length")
            row.total_bytes += len(data)
            session.add(QuoteChunk(upload_id=str(identifier), position=position, payload=data))
        return {"received": len(data)}


def preview_upload(db, identifier):
    with db.transaction() as session:
        row = upload_record(session, identifier)
        if row.status == "completed":
            raise StateConflict("Import already confirmed; use its saved result")
        if row.preview:
            return row.preview
        chunks = list(session.scalars(select(QuoteChunk).where(QuoteChunk.upload_id == str(identifier)).order_by(QuoteChunk.position)))
        if not chunks or [c.position for c in chunks] != list(range(len(chunks))) or row.total_bytes != row.expected_bytes:
            raise StateConflict("Incomplete upload; supply all chunks and exact file length")
        payload = b"".join(c.payload for c in chunks)
        try:
            result = parse_quotes(payload, row.filename, session, row.dataset_id)
        except ValueError as exc:
            result = {"quotes": [], "findings": [{"row": None, "message": str(exc)}],
                      "error_count": 1, "row_count": 0, "can_confirm": False}
        result["preview_hash"] = digest(result)
        row.preview, row.status = result, "previewed"
        return result


def confirm_upload(db, identifier, body, supplied_by):
    with db.transaction() as session:
        row = upload_record(session, identifier)
        if row.result:
            if row.result["preview_hash"] != body.preview_hash:
                raise StateConflict("Confirmation hash does not match the committed preview")
            return row.result
        if not row.preview or not row.preview["can_confirm"] or row.preview["preview_hash"] != body.preview_hash:
            raise StateConflict("Review a valid preview and explicitly confirm its exact hash")
        dataset_record(session, row.dataset_id, lock=True)
        # Every insert and the receipt share this transaction. A single failure rolls all back.
        quotes = [insert_quote(session, row.dataset_id, QuoteInput.model_validate(q), supplied_by) for q in row.preview["quotes"]]
        result = {"id": row.id, "dataset_id": row.dataset_id, "quote_ids": [q["id"] for q in quotes],
                  "count": len(quotes), "preview_hash": body.preview_hash}
        row.result, row.status, row.preview = result, "completed", None
        session.execute(delete(QuoteChunk).where(QuoteChunk.upload_id == row.id))
        return result
