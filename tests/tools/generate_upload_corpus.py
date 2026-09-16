#!/usr/bin/env python3
"""Generate a benign, dependency-free file corpus for upload falsification probes.

The helper intentionally uses only Python's standard library so installing the
skill never requires mutating the user's Python environment. Small raster
fixtures are pre-encoded, valid images generated when the skill is built;
DOCX/PPTX probes are minimal valid OOXML/OPC packages assembled with ``zipfile``.

This corpus is *not* a statement that a target must support every format.
Classify every probe against the target's claimed contract before judging it.
"""
from __future__ import annotations

import argparse
import base64
import json
import zipfile
from pathlib import Path

# Tiny, genuinely encoded raster images. A/B variants are materially distinct.
# They are fixtures, not expected outputs from the target application.
_RASTER_B64 = {
    "valid-a.jpg": "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAALABEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDk6KKK4z9KCiiigD//2Q==",
    "valid-b.jpeg": "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAATAA0DASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDHooorA+KCiiigD//Z",
    "valid-a.png": "iVBORw0KGgoAAAANSUhEUgAAABEAAAALCAIAAAAWQvFQAAAAF0lEQVR4nGM8IWfDQCJgIlXDqJ5hqQcAoS4BOPOZcOAAAAAASUVORK5CYII=",
    "valid-b.png": "iVBORw0KGgoAAAANSUhEUgAAAA0AAAATCAIAAADEVRDAAAAAGUlEQVR4nGMUmRbFQARgIkbRqLpRdUNEHQA9HwEqIsVskAAAAABJRU5ErkJggg==",
    "valid-a.gif": "R0lGODdhEQALAIEAAMgePAAAAAAAAAAAACwAAAAAEQALAAAIGAABCBxIsKDBgwgTKlzIsKHDhxAjSoQYEAA7",
    "valid-a.webp": "UklGRkQAAABXRUJQVlA4IDgAAACwAgCdASoRAAsAPm0skUWkIqGYBABABsSgCdAC0kAA/u+9Vri5siKP/srH/+lY//0rH8OcPYYAAA==",
    "valid-a.tif": "SUkqAAgAAAAKAAABBAABAAAAEQAAAAEBBAABAAAACwAAAAIBAwADAAAAhgAAAAMBAwABAAAAAQAAAAYBAwABAAAAAgAAABEBBAABAAAAjAAAABUBAwABAAAAAwAAABYBBAABAAAACwAAABcBBAABAAAAMQIAABwBAwABAAAAAQAAAAAAAAAIAAgACADIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjzIHjw=",
    "valid-b.tiff": "SUkqAAgAAAAKAAABBAABAAAADQAAAAEBBAABAAAAEwAAAAIBAwADAAAAhgAAAAMBAwABAAAAAQAAAAYBAwABAAAAAgAAABEBBAABAAAAjAAAABUBAwABAAAAAwAAABYBBAABAAAAEwAAABcBBAABAAAA5QIAABwBAwABAAAAAQAAAAAAAAAIAAgACAAUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUlloUllo=",
}


def _write_ooxml_docx(path: Path) -> None:
    """Create a minimal WordprocessingML package using only stdlib."""
    parts = {
        "[Content_Types].xml": """<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>
<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">
 <Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>
 <Default Extension=\"xml\" ContentType=\"application/xml\"/>
 <Override PartName=\"/word/document.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/>
</Types>""",
        "_rels/.rels": """<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>
<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">
 <Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"word/document.xml\"/>
</Relationships>""",
        "word/document.xml": """<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>
<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"><w:body><w:p><w:r><w:t>Benign DOCX upload probe</w:t></w:r></w:p><w:sectPr/></w:body></w:document>""",
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in parts.items():
            zf.writestr(name, text)


def _write_ooxml_pptx(path: Path) -> None:
    """Create a minimal PresentationML package using only stdlib."""
    parts = {
        "[Content_Types].xml": """<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>
<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">
 <Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>
 <Default Extension=\"xml\" ContentType=\"application/xml\"/>
 <Override PartName=\"/ppt/presentation.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml\"/>
 <Override PartName=\"/ppt/slides/slide1.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.presentationml.slide+xml\"/>
</Types>""",
        "_rels/.rels": """<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>
<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">
 <Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"ppt/presentation.xml\"/>
</Relationships>""",
        "ppt/presentation.xml": """<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>
<p:presentation xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\" xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\" xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\"><p:sldIdLst><p:sldId id=\"256\" r:id=\"rId1\"/></p:sldIdLst><p:sldSz cx=\"9144000\" cy=\"6858000\"/><p:notesSz cx=\"6858000\" cy=\"9144000\"/></p:presentation>""",
        "ppt/_rels/presentation.xml.rels": """<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>
<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"><Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide\" Target=\"slides/slide1.xml\"/></Relationships>""",
        "ppt/slides/slide1.xml": """<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>
<p:sld xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\" xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\" xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id=\"1\" name=\"\"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/><p:sp><p:nvSpPr><p:cNvPr id=\"2\" name=\"Probe\"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang=\"en-US\"/><a:t>Benign PPTX upload probe</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>""",
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in parts.items():
            zf.writestr(name, text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("output", type=Path)
    args = ap.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    made: list[str] = []

    for name, encoded in _RASTER_B64.items():
        (out / name).write_bytes(base64.b64decode(encoded, validate=True))
        made.append(name)

    (out / "valid-a.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="17" height="11"><rect width="17" height="11" fill="red"/></svg>',
        encoding="utf-8",
    )
    made.append("valid-a.svg")
    (out / "valid-a.eps").write_text(
        "%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 17 11\nnewpath 0 0 moveto 17 11 lineto stroke\nshowpage\n",
        encoding="ascii",
    )
    made.append("valid-a.eps")

    # Representative non-image inputs.
    (out / "not-image.txt").write_text("plain text upload probe\n", encoding="utf-8")
    (out / "not-image.py").write_text("print('benign upload probe')\n", encoding="utf-8")
    (out / "not-image.json").write_text(json.dumps({"kind": "benign-upload-probe"}), encoding="utf-8")
    (out / "not-image.pdf").write_bytes(
        b"%PDF-1.1\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
    )
    made += ["not-image.txt", "not-image.py", "not-image.json", "not-image.pdf"]

    with zipfile.ZipFile(out / "not-image.zip", "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("probe.txt", "benign archive upload probe")
    made.append("not-image.zip")

    _write_ooxml_docx(out / "not-image.docx")
    _write_ooxml_pptx(out / "not-image.pptx")
    made += ["not-image.docx", "not-image.pptx"]

    # Structural-invalid probes.
    (out / "empty.png").write_bytes(b"")
    (out / "truncated.png").write_bytes(b"\x89PNG\r\n\x1a\nTRUNCATED")
    (out / "renamed-text.png").write_text("this is not an image", encoding="utf-8")
    made += ["empty.png", "truncated.png", "renamed-text.png"]

    print(f"generated {len(made)} files in {out}")
    for name in made:
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
