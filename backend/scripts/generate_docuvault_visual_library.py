"""Generate the deterministic, fictional DocuVault visual reference library.

The generated profiles are deliberately separate from official/generic metadata
profiles. Every rendered page states that it is a synthetic demonstration, and
all people, identifiers, issuers, payloads, signatures, seals, and results are
fictional. Production code may use these assets only for controlled synthetic
demonstrations; evaluation ground truth remains under ``samples`` and is never
referenced by a production profile.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import cv2
import fitz
import numpy as np
from PIL import Image

from backend.app.docuvault.visual_assets import (
    compute_visual_fingerprint,
    region_mask_image,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROFILE_CATALOG = PROJECT_ROOT / "backend" / "docuvault" / "profiles" / "core.profile.json"
SYNTHETIC_PROFILE_CATALOG = (
    PROJECT_ROOT / "backend" / "docuvault" / "profiles" / "synthetic-visual.profile.json"
)
ASSET_ROOT = PROJECT_ROOT / "backend" / "docuvault" / "assets" / "synthetic"
EVALUATION_ROOT = PROJECT_ROOT / "samples" / "docuvault-visual-evaluation"
PAGE_WIDTH = 595.0
PAGE_HEIGHT = 842.0
RENDER_LONGEST = 1200
THUMBNAIL_LONGEST = 280
GENERATOR_VERSION = "1.0.0"
RETRIEVAL_DATE = "2026-08-29"

FIXED_METADATA = {
    "author": "DocuVerify Visual Library Generator",
    "subject": "Fictional detector-evaluation fixture; not a real document",
    "keywords": "docuverify, synthetic, demonstration, fictional",
    "creator": "DocuVerify deterministic generator",
    "producer": "PyMuPDF",
    "creationDate": "D:20420829000000Z",
    "modDate": "D:20420829000000Z",
}


@dataclass(frozen=True, slots=True)
class VisualSpec:
    source_profile_id: str
    profile_id: str
    slug: str
    display_name: str
    issuer: str
    title: str
    layout: str
    pages: int = 1


@dataclass(frozen=True, slots=True)
class PageRegions:
    fixed: tuple[dict[str, Any], ...]
    variable: tuple[dict[str, Any], ...]
    security: dict[str, tuple[dict[str, Any], ...]]
    named_boxes: dict[str, dict[str, float]]


SPECS = (
    VisualSpec("in.cbse.class10.generic.v1", "synthetic.docuverify.cbse-class10.v1", "cbse-class10", "Synthetic Class 10 marksheet", "Meridian Learning Board", "CLASS 10 STATEMENT OF MARKS", "academic"),
    VisualSpec("in.cbse.class12.generic.v1", "synthetic.docuverify.cbse-class12.v1", "cbse-class12", "Synthetic Class 12 marksheet", "Aurora Senior Learning Board", "CLASS 12 STATEMENT OF MARKS", "academic"),
    VisualSpec("generic.university.grade-cgpa.v1", "synthetic.docuverify.university-marksheet.v1", "university-marksheet", "Synthetic university marksheet", "Northstar Institute of Learning", "SEMESTER MARKSHEET", "academic"),
    VisualSpec("synthetic.lumen-grove.achievement-record.v1", "synthetic.lumen-grove.achievement-record.v1", "cgpa-certificate", "Synthetic CGPA certificate", "Lumen Grove Synthetic Record", "CGPA ACHIEVEMENT CERTIFICATE", "academic"),
    VisualSpec("generic.university.degree.v1", "synthetic.docuverify.degree-certificate.v1", "degree-certificate", "Synthetic degree certificate", "Silver Oak Demonstration University", "DEGREE CERTIFICATE", "certificate"),
    VisualSpec("in.uidai.aadhaar-style.v1", "synthetic.docuverify.aadhaar-style.v1", "aadhaar-style", "Synthetic Aadhaar-style identity document", "Civic Identity Demonstration Lab", "IDENTITY INFORMATION CARD", "identity", 2),
    VisualSpec("in.eci.voter-card.v1", "synthetic.docuverify.voter-card.v1", "voter-card", "Synthetic voter-card-style document", "Fictional Electoral Registry", "ELECTOR INFORMATION CARD", "identity", 2),
    VisualSpec("in.nfsa.ration-card.generic.v1", "synthetic.docuverify.ration-card.v1", "ration-card", "Synthetic ration-card-style document", "Fictional Household Services Board", "HOUSEHOLD BENEFIT CARD", "identity", 2),
    VisualSpec("generic.university.student-id.v1", "synthetic.docuverify.university-id.v1", "university-id", "Synthetic university identity card", "Lakeview Demonstration College", "STUDENT IDENTITY CARD", "identity", 2),
    VisualSpec("in.morth.driving-licence.generic.v1", "synthetic.docuverify.driving-licence.v1", "driving-licence", "Synthetic driving-licence-style document", "Fictional Mobility Registry", "DRIVER INFORMATION CARD", "identity", 2),
    VisualSpec("in.mea.passport.generic.v1", "synthetic.docuverify.passport.v1", "passport", "Synthetic passport-style document", "Republic of Northstar Demonstration Office", "TRAVEL DOCUMENT", "passport", 2),
    VisualSpec("in.itd.pan-card.v1", "synthetic.docuverify.pan-style.v1", "pan-style", "Synthetic PAN-style document", "Fictional Revenue Registry", "TAX IDENTITY CARD", "identity", 2),
    VisualSpec("generic.education.fee-receipt.v1", "synthetic.docuverify.fee-receipt.v1", "fee-receipt", "Synthetic fee receipt", "Northstar Institute of Learning", "FEE RECEIPT", "receipt"),
    VisualSpec("generic.education.internship-certificate.v1", "synthetic.docuverify.internship-certificate.v1", "internship-certificate", "Synthetic internship certificate", "Aster Systems Training Studio", "INTERNSHIP CERTIFICATE", "certificate"),
    VisualSpec("generic.education.bonafide-certificate.v1", "synthetic.docuverify.bonafide-certificate.v1", "bonafide-certificate", "Synthetic bonafide certificate", "Lakeview Demonstration College", "BONAFIDE CERTIFICATE", "certificate"),
    VisualSpec("generic.education.noc-certificate.v1", "synthetic.docuverify.noc-certificate.v1", "noc-certificate", "Synthetic NOC certificate", "Silver Oak Demonstration University", "NO OBJECTION CERTIFICATE", "certificate"),
    VisualSpec("in.civil.birth-certificate.generic.v1", "synthetic.docuverify.birth-certificate.v1", "birth-certificate", "Synthetic birth certificate", "Fictional Civil Records Office", "BIRTH REGISTRATION CERTIFICATE", "civil"),
    VisualSpec("in.civil.death-certificate.generic.v1", "synthetic.docuverify.death-certificate.v1", "death-certificate", "Synthetic death certificate", "Fictional Civil Records Office", "DEATH REGISTRATION CERTIFICATE", "civil"),
    VisualSpec("generic.civil.proof-of-address.v1", "synthetic.docuverify.proof-of-address.v1", "proof-of-address", "Synthetic proof-of-address document", "Harbor Utilities Demonstration Service", "PROOF OF ADDRESS STATEMENT", "letter"),
    VisualSpec("in.mha.visa-document.v1", "synthetic.docuverify.visa-style.v1", "visa-style", "Synthetic visa-style document", "Northstar Visitor Services", "VISITOR PERMIT", "visa"),
)

VALUES = {
    "reference-a": {
        "name": "KAVYA SRINIVASAN",
        "identifier": "SYN-DV-2042-A17",
        "address": "17 ORBIT LANE, FICTIONAL CITY",
        "date": "17 JUNE 2042",
        "serial": "DEMO-2042-A017",
        "grade": "A+",
        "cgpa": "8.72",
        "mark": "91",
        "amount": "18,450.00",
        "qr": "DV:SYNTHETIC:REFERENCE-A",
        "initials": "KS",
    },
    "reference-b": {
        "name": "ARJUN MENON",
        "identifier": "SYN-DV-2042-B43",
        "address": "43 COMET ROAD, DEMO DISTRICT",
        "date": "21 JULY 2042",
        "serial": "DEMO-2042-B043",
        "grade": "A",
        "cgpa": "8.31",
        "mark": "84",
        "amount": "19,275.00",
        "qr": "DV:SYNTHETIC:REFERENCE-B",
        "initials": "AM",
    },
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _safe_reset(path: Path) -> None:
    resolved = path.resolve(strict=False)
    root = PROJECT_ROOT.resolve()
    if root not in resolved.parents or resolved in {root, root / "backend", root / "samples"}:
        raise RuntimeError(f"refusing to reset unsafe generated path: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True, exist_ok=True)


def _box(rect: fitz.Rect) -> dict[str, float]:
    return {
        "x": round(rect.x0 / PAGE_WIDTH, 6),
        "y": round(rect.y0 / PAGE_HEIGHT, 6),
        "width": round(rect.width / PAGE_WIDTH, 6),
        "height": round(rect.height / PAGE_HEIGHT, 6),
    }


def _region(region_id: str, page: int, rect: fitz.Rect, label: str) -> dict[str, Any]:
    return {"region_id": region_id, "page": page, "box": _box(rect), "label": label}


def _draw_emblem(page: fitz.Page, center: fitz.Point) -> None:
    page.draw_circle(center, 27, color=(0.94, 0.48, 0.08), fill=(0.04, 0.13, 0.24), width=2)
    page.draw_circle(center, 20, color=(0.10, 0.58, 0.31), width=1)
    page.insert_text((center.x - 13, center.y + 6), "DV", fontsize=14, fontname="hebo", color=(1, 1, 1))


def _draw_common(page: fitz.Page, spec: VisualSpec, page_number: int) -> None:
    page.draw_rect(page.rect, color=None, fill=(0.985, 0.982, 0.972), width=0)
    page.draw_rect(fitz.Rect(24, 24, 571, 818), color=(0.04, 0.13, 0.24), width=2.2)
    page.draw_rect(fitz.Rect(29, 29, 566, 813), color=(0.94, 0.48, 0.08), width=0.8)
    page.draw_rect(fitz.Rect(30, 30, 565, 122), color=None, fill=(0.04, 0.13, 0.24))
    page.draw_rect(fitz.Rect(30, 122, 565, 128), color=None, fill=(0.10, 0.58, 0.31))
    _draw_emblem(page, fitz.Point(76, 76))
    page.insert_text((118, 62), spec.issuer.upper(), fontsize=15, fontname="hebo", color=(1, 1, 1))
    page.insert_text((118, 84), "FICTIONAL ISSUER - DETECTOR EVALUATION ONLY", fontsize=8, fontname="helv", color=(0.84, 0.89, 0.94))
    page.insert_text((118, 104), spec.title, fontsize=11, fontname="hebo", color=(1.0, 0.68, 0.25))
    page.insert_text((44, 785), "SYNTHETIC DEMONSTRATION - NOT A REAL OR ISSUABLE DOCUMENT", fontsize=8.4, fontname="hebo", color=(0.58, 0.10, 0.10))
    page.insert_text((44, 802), f"DOCUVERIFY VISUAL LIBRARY {GENERATOR_VERSION}  |  PAGE {page_number}/{spec.pages}", fontsize=7.2, fontname="helv", color=(0.30, 0.36, 0.41))


def _draw_label_value(
    page: fitz.Page,
    rect: fitz.Rect,
    label: str,
    value: str,
    *,
    mutation: str | None,
    field: str,
) -> None:
    page.draw_rect(rect, color=(0.76, 0.79, 0.80), fill=(1, 1, 1), width=0.65)
    page.insert_text((rect.x0 + 10, rect.y0 + 16), label, fontsize=7, fontname="hebo", color=(0.30, 0.36, 0.40))
    x = rect.x0 + 136 + (12 if mutation == "position_shift" and field == "identifier" else 0)
    baseline = rect.y0 + 29 + (5 if mutation == "baseline_shift" and field in {"date", "identifier"} else 0)
    font = "hebo" if mutation == "font_weight_change" and field == "name" else "helv"
    size = 12.6 if font == "hebo" else 11.2
    if mutation == "pasted_background" and field in {"grade", "amount", "address"}:
        page.draw_rect(fitz.Rect(x - 5, rect.y0 + 18, rect.x1 - 8, rect.y1 - 6), color=(0.86, 0.86, 0.82), fill=(0.97, 0.965, 0.93), width=0.9)
    page.insert_text((x, baseline), value, fontsize=size, fontname=font, color=(0.05, 0.12, 0.20))


def _qr_png(payload: str, *, replacement: bool = False) -> bytes:
    actual = payload + (":ALTERED" if replacement else "")
    encoded = cv2.QRCodeEncoder_create().encode(actual)
    encoded = cv2.copyMakeBorder(encoded, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=255)
    encoded = cv2.resize(encoded, (256, 256), interpolation=cv2.INTER_NEAREST)
    ok, data = cv2.imencode(".png", encoded, [cv2.IMWRITE_PNG_COMPRESSION, 9])
    if not ok:
        raise RuntimeError("could not encode synthetic QR fixture")
    return data.tobytes()


def _draw_photo(page: fitz.Page, rect: fitz.Rect, initials: str, *, pasted: bool) -> None:
    if pasted:
        page.draw_rect(fitz.Rect(rect.x0 - 4, rect.y0 - 4, rect.x1 + 5, rect.y1 + 5), color=(0.55, 0.55, 0.53), fill=(1, 1, 1), width=1.2)
    page.draw_rect(rect, color=(0.09, 0.32, 0.43), fill=(0.88, 0.94, 0.95), width=1)
    page.draw_circle(fitz.Point((rect.x0 + rect.x1) / 2, rect.y0 + 34), 17, color=None, fill=(0.94, 0.70, 0.47))
    page.draw_rect(fitz.Rect(rect.x0 + 18, rect.y0 + 53, rect.x1 - 18, rect.y1 - 14), color=None, fill=(0.11, 0.35, 0.47))
    page.insert_text((rect.x0 + 26, rect.y1 - 19), initials, fontsize=10, fontname="hebo", color=(1, 1, 1))


def _draw_signature(page: fitz.Page, rect: fitz.Rect, exemplar: str, *, pasted: bool) -> None:
    if pasted:
        page.draw_rect(fitz.Rect(rect.x0 - 4, rect.y0 - 3, rect.x1 + 4, rect.y1 + 3), color=(0.78, 0.76, 0.70), fill=(0.995, 0.99, 0.96), width=1)
    offset = 7 if exemplar == "reference-b" else 0
    points = [
        fitz.Point(rect.x0 + 4, rect.y0 + 25),
        fitz.Point(rect.x0 + 28 + offset, rect.y0 + 8),
        fitz.Point(rect.x0 + 45, rect.y0 + 28),
        fitz.Point(rect.x0 + 72 + offset, rect.y0 + 12),
        fitz.Point(rect.x0 + 110, rect.y0 + 26),
    ]
    shape = page.new_shape()
    for left, right in zip(points, points[1:]):
        shape.draw_line(left, right)
    shape.finish(color=(0.08, 0.16, 0.32), width=1.5)
    shape.commit()
    page.draw_line(fitz.Point(rect.x0, rect.y1), fitz.Point(rect.x1, rect.y1), color=(0.35, 0.38, 0.40), width=0.6)


def _draw_seal(page: fitz.Page, rect: fitz.Rect, *, displaced: bool) -> fitz.Rect:
    if displaced:
        rect = fitz.Rect(rect.x0 - 20, rect.y0 + 8, rect.x1 - 20, rect.y1 + 8)
    center = fitz.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
    radius = min(rect.width, rect.height) / 2
    page.draw_circle(center, radius, color=(0.10, 0.58, 0.31), fill=(0.91, 0.97, 0.92), width=1.8)
    page.draw_circle(center, radius - 7, color=(0.94, 0.48, 0.08), width=0.8)
    page.insert_text((center.x - 19, center.y + 3), "DEMO", fontsize=8, fontname="hebo", color=(0.07, 0.37, 0.20))
    return rect


def _draw_academic(page: fitz.Page, values: dict[str, str], exemplar: str, mutation: str | None) -> dict[str, fitz.Rect]:
    boxes = {
        "name": fitz.Rect(55, 158, 390, 199),
        "identifier": fitz.Rect(55, 205, 390, 246),
        "date": fitz.Rect(55, 252, 390, 293),
        "grade": fitz.Rect(55, 553, 390, 594),
        "mark": fitz.Rect(398, 386, 456, 425),
        "table_grade": fitz.Rect(470, 386, 532, 425),
        "qr": fitz.Rect(425, 168, 510, 253),
        "signature": fitz.Rect(355, 676, 505, 716),
        "seal": fitz.Rect(72, 660, 132, 720),
        "border": fitz.Rect(24, 24, 571, 818),
    }
    _draw_label_value(page, boxes["name"], "CANDIDATE NAME", values["name"], mutation=mutation, field="name")
    _draw_label_value(page, boxes["identifier"], "REGISTRATION ID", values["identifier"], mutation=mutation, field="identifier")
    _draw_label_value(page, boxes["date"], "ISSUE DATE", values["date"], mutation=mutation, field="date")
    qr_rect = boxes["qr"] + (12, 4, 12, 4) if mutation == "qr_displacement" else boxes["qr"]
    page.insert_image(qr_rect, stream=_qr_png(values["qr"], replacement=mutation == "qr_replacement"), keep_proportion=False)
    page.insert_text((422, 267), "SYNTHETIC QR", fontsize=6.5, fontname="hebo", color=(0.42, 0.16, 0.14))
    page.draw_rect(fitz.Rect(55, 320, 540, 530), color=(0.12, 0.20, 0.28), fill=(1, 1, 1), width=0.8)
    headers = (("SUBJECT", 65), ("MAX", 330), ("MARK", 402), ("GRADE", 474))
    page.draw_rect(fitz.Rect(55, 320, 540, 350), color=None, fill=(0.04, 0.13, 0.24))
    for text, x in headers:
        page.insert_text((x, 340), text, fontsize=7, fontname="hebo", color=(1, 1, 1))
    rows = (("Data Systems", "100", "88", "A"), ("Applied Mathematics", "100", values["mark"], values["grade"]), ("Verification Lab", "100", "86", "A"), ("Professional Practice", "100", "82", "B+"))
    for index, row in enumerate(rows):
        y = 374 + index * 38
        if index % 2:
            page.draw_rect(fitz.Rect(56, y - 18, 539, y + 14), color=None, fill=(0.95, 0.96, 0.96))
        for text, x in zip(row, (65, 342, 412, 488)):
            page.insert_text((x, y), text, fontsize=8.5, fontname="hebo" if x > 400 else "helv", color=(0.06, 0.12, 0.19))
    _draw_label_value(page, boxes["grade"], "CGPA / RESULT", values["cgpa"] if mutation != "logical_value_change" else "14.20", mutation=mutation, field="grade")
    boxes["seal"] = _draw_seal(page, boxes["seal"], displaced=mutation == "seal_displacement")
    _draw_signature(page, boxes["signature"], exemplar, pasted=mutation == "signature_paste")
    return boxes


def _draw_identity(page: fitz.Page, values: dict[str, str], exemplar: str, mutation: str | None) -> dict[str, fitz.Rect]:
    boxes = {
        "name": fitz.Rect(190, 180, 520, 222),
        "identifier": fitz.Rect(190, 232, 520, 274),
        "date": fitz.Rect(190, 284, 520, 326),
        "address": fitz.Rect(75, 370, 520, 440),
        "photo": fitz.Rect(66, 178, 164, 320),
        "qr": fitz.Rect(395, 500, 500, 605),
        "serial": fitz.Rect(88, 556, 338, 586),
        "signature": fitz.Rect(75, 646, 225, 686),
        "seal": fitz.Rect(272, 625, 347, 700),
        "border": fitz.Rect(24, 24, 571, 818),
    }
    _draw_photo(page, boxes["photo"], values["initials"], pasted=mutation == "photo_paste")
    _draw_label_value(page, boxes["name"], "NAME", values["name"], mutation=mutation, field="name")
    _draw_label_value(page, boxes["identifier"], "DOCUMENT ID", values["identifier"], mutation=mutation, field="identifier")
    _draw_label_value(page, boxes["date"], "VALID FROM", values["date"], mutation=mutation, field="date")
    _draw_label_value(page, boxes["address"], "ADDRESS", values["address"], mutation=mutation, field="address")
    page.draw_rect(fitz.Rect(75, 462, 350, 608), color=(0.78, 0.81, 0.82), fill=(1, 1, 1), width=0.7)
    page.insert_text((92, 488), "CLASS / STATUS", fontsize=7, fontname="hebo", color=(0.30, 0.36, 0.40))
    page.insert_text((92, 515), "DEMONSTRATION HOLDER", fontsize=11, fontname="hebo", color=(0.05, 0.12, 0.20))
    page.insert_text((92, 548), "SERIAL", fontsize=7, fontname="hebo", color=(0.30, 0.36, 0.40))
    page.insert_text((92, 575), values["serial"], fontsize=10, fontname="helv", color=(0.05, 0.12, 0.20))
    qr_rect = boxes["qr"] + (16, -8, 16, -8) if mutation == "qr_displacement" else boxes["qr"]
    page.insert_image(qr_rect, stream=_qr_png(values["qr"], replacement=mutation == "qr_replacement"), keep_proportion=False)
    boxes["seal"] = _draw_seal(page, boxes["seal"], displaced=mutation == "seal_displacement")
    _draw_signature(page, boxes["signature"], exemplar, pasted=mutation == "signature_paste")
    return boxes


def _draw_identity_back(page: fitz.Page, values: dict[str, str], exemplar: str) -> dict[str, fitz.Rect]:
    boxes = {
        "name": fitz.Rect(70, 186, 520, 228),
        "identifier": fitz.Rect(70, 238, 520, 280),
        "address": fitz.Rect(70, 290, 520, 365),
        "date": fitz.Rect(70, 375, 520, 417),
        "qr": fitz.Rect(405, 475, 505, 575),
        "signature": fitz.Rect(78, 646, 228, 686),
        "seal": fitz.Rect(275, 625, 350, 700),
        "border": fitz.Rect(24, 24, 571, 818),
    }
    page.insert_text((70, 160), "REVERSE SIDE - FICTIONAL HOLDER DETAILS", fontsize=10, fontname="hebo", color=(0.10, 0.40, 0.25))
    _draw_label_value(page, boxes["name"], "HOLDER", values["name"], mutation=None, field="name")
    _draw_label_value(page, boxes["identifier"], "DOCUMENT ID", values["identifier"], mutation=None, field="identifier")
    _draw_label_value(page, boxes["address"], "DEMONSTRATION ADDRESS", values["address"], mutation=None, field="address")
    _draw_label_value(page, boxes["date"], "EXPIRY / REVIEW DATE", values["date"], mutation=None, field="date")
    page.draw_rect(fitz.Rect(70, 455, 370, 585), color=(0.78, 0.81, 0.82), fill=(1, 1, 1), width=0.7)
    page.insert_text((88, 485), "TERMS OF SYNTHETIC USE", fontsize=8, fontname="hebo", color=(0.10, 0.40, 0.25))
    page.insert_textbox(fitz.Rect(88, 505, 350, 565), "This side contains no valid entitlement, licence, account, or identity information. It exists only for local visual testing.", fontsize=8.5, fontname="helv", color=(0.23, 0.29, 0.33), lineheight=1.3)
    page.insert_image(boxes["qr"], stream=_qr_png(values["qr"] + ":BACK"), keep_proportion=False)
    _draw_signature(page, boxes["signature"], exemplar, pasted=False)
    boxes["seal"] = _draw_seal(page, boxes["seal"], displaced=False)
    return boxes


def _draw_certificate(page: fitz.Page, values: dict[str, str], exemplar: str, mutation: str | None) -> dict[str, fitz.Rect]:
    boxes = {
        "name": fitz.Rect(88, 260, 507, 313),
        "identifier": fitz.Rect(88, 455, 507, 500),
        "date": fitz.Rect(88, 510, 507, 555),
        "grade": fitz.Rect(88, 370, 507, 423),
        "signature": fitz.Rect(88, 650, 245, 695),
        "seal": fitz.Rect(412, 620, 505, 713),
        "border": fitz.Rect(24, 24, 571, 818),
    }
    page.insert_text((77, 180), "THIS FICTIONAL RECORD CERTIFIES THAT", fontsize=10, fontname="helv", color=(0.28, 0.34, 0.38))
    _draw_label_value(page, boxes["name"], "RECIPIENT", values["name"], mutation=mutation, field="name")
    page.insert_textbox(fitz.Rect(88, 325, 507, 366), "completed the synthetic programme in Applied Document Verification for local detector evaluation.", fontsize=10, fontname="helv", color=(0.20, 0.27, 0.31), align=1)
    _draw_label_value(page, boxes["grade"], "AWARD / RESULT", values["grade"], mutation=mutation, field="grade")
    _draw_label_value(page, boxes["identifier"], "CERTIFICATE ID", values["identifier"], mutation=mutation, field="identifier")
    _draw_label_value(page, boxes["date"], "ISSUE DATE", values["date"], mutation=mutation, field="date")
    page.draw_line(fitz.Point(88, 592), fitz.Point(507, 592), color=(0.94, 0.48, 0.08), width=1)
    _draw_signature(page, boxes["signature"], exemplar, pasted=mutation == "signature_paste")
    boxes["seal"] = _draw_seal(page, boxes["seal"], displaced=mutation == "seal_displacement")
    if mutation == "border_interrupt":
        page.draw_rect(fitz.Rect(22, 390, 37, 485), color=None, fill=(0.985, 0.982, 0.972))
    return boxes


def _draw_receipt(page: fitz.Page, values: dict[str, str], exemplar: str, mutation: str | None) -> dict[str, fitz.Rect]:
    boxes = {
        "name": fitz.Rect(55, 166, 390, 207),
        "identifier": fitz.Rect(55, 213, 390, 254),
        "date": fitz.Rect(55, 260, 390, 301),
        "amount": fitz.Rect(55, 550, 390, 597),
        "qr": fitz.Rect(430, 168, 515, 253),
        "signature": fitz.Rect(350, 660, 505, 700),
        "seal": fitz.Rect(75, 642, 142, 709),
        "border": fitz.Rect(24, 24, 571, 818),
    }
    _draw_label_value(page, boxes["name"], "RECEIVED FROM", values["name"], mutation=mutation, field="name")
    _draw_label_value(page, boxes["identifier"], "RECEIPT ID", values["identifier"], mutation=mutation, field="identifier")
    _draw_label_value(page, boxes["date"], "PAYMENT DATE", values["date"], mutation=mutation, field="date")
    page.insert_image(boxes["qr"], stream=_qr_png(values["qr"], replacement=mutation == "qr_replacement"), keep_proportion=False)
    page.draw_rect(fitz.Rect(55, 334, 540, 520), color=(0.12, 0.20, 0.28), fill=(1, 1, 1), width=0.8)
    page.draw_rect(fitz.Rect(55, 334, 540, 365), color=None, fill=(0.04, 0.13, 0.24))
    for text, x in (("DESCRIPTION", 68), ("TERM", 350), ("AMOUNT", 455)):
        page.insert_text((x, 355), text, fontsize=7, fontname="hebo", color=(1, 1, 1))
    for index, row in enumerate((("Tuition demonstration fee", "2042-A", "12,000"), ("Laboratory demonstration fee", "2042-A", "4,500"), ("Library demonstration fee", "2042-A", "1,950"))):
        y = 397 + index * 43
        for text, x in zip(row, (68, 350, 455)):
            page.insert_text((x, y), text, fontsize=8.2, fontname="helv", color=(0.06, 0.12, 0.19))
    total = "99,999.00" if mutation == "logical_value_change" else values["amount"]
    _draw_label_value(page, boxes["amount"], "TOTAL PAID", total, mutation=mutation, field="amount")
    boxes["seal"] = _draw_seal(page, boxes["seal"], displaced=mutation == "seal_displacement")
    _draw_signature(page, boxes["signature"], exemplar, pasted=mutation == "signature_paste")
    return boxes


def _draw_civil_or_letter(page: fitz.Page, values: dict[str, str], exemplar: str, mutation: str | None, *, letter: bool) -> dict[str, fitz.Rect]:
    boxes = {
        "name": fitz.Rect(70, 202, 525, 247),
        "identifier": fitz.Rect(70, 257, 525, 302),
        "date": fitz.Rect(70, 312, 525, 357),
        "address": fitz.Rect(70, 367, 525, 437),
        "grade": fitz.Rect(70, 457, 525, 502),
        "signature": fitz.Rect(82, 650, 245, 695),
        "seal": fitz.Rect(414, 620, 507, 713),
        "border": fitz.Rect(24, 24, 571, 818),
    }
    page.insert_text((70, 174), "FICTIONAL RECORD DETAILS" if not letter else "FICTIONAL ACCOUNT HOLDER DETAILS", fontsize=10, fontname="hebo", color=(0.10, 0.40, 0.25))
    _draw_label_value(page, boxes["name"], "SUBJECT NAME", values["name"], mutation=mutation, field="name")
    _draw_label_value(page, boxes["identifier"], "RECORD NUMBER", values["identifier"], mutation=mutation, field="identifier")
    _draw_label_value(page, boxes["date"], "RECORDED DATE", values["date"], mutation=mutation, field="date")
    _draw_label_value(page, boxes["address"], "REGISTERED ADDRESS", values["address"], mutation=mutation, field="address")
    _draw_label_value(page, boxes["grade"], "STATUS", "DEMONSTRATION ONLY", mutation=mutation, field="grade")
    page.insert_textbox(fitz.Rect(72, 530, 520, 595), "This statement is a generated detector fixture. It is not evidence of a civil event, residence, entitlement, identity, or account.", fontsize=9.5, fontname="helv", color=(0.23, 0.29, 0.33), lineheight=1.35)
    _draw_signature(page, boxes["signature"], exemplar, pasted=mutation == "signature_paste")
    boxes["seal"] = _draw_seal(page, boxes["seal"], displaced=mutation == "seal_displacement")
    if mutation == "border_interrupt":
        page.draw_rect(fitz.Rect(559, 390, 574, 490), color=None, fill=(0.985, 0.982, 0.972))
    return boxes


def _draw_passport_page(page: fitz.Page, values: dict[str, str], exemplar: str, mutation: str | None, page_number: int) -> dict[str, fitz.Rect]:
    if page_number == 2:
        boxes = {
            "name": fitz.Rect(72, 208, 520, 251),
            "identifier": fitz.Rect(72, 261, 520, 304),
            "date": fitz.Rect(72, 314, 520, 357),
            "address": fitz.Rect(72, 367, 520, 430),
            "qr": fitz.Rect(412, 510, 505, 603),
            "signature": fitz.Rect(78, 650, 230, 690),
            "seal": fitz.Rect(282, 625, 362, 705),
            "border": fitz.Rect(24, 24, 571, 818),
        }
        page.insert_text((72, 174), "OBSERVATIONS AND DEMONSTRATION DATA", fontsize=11, fontname="hebo", color=(0.10, 0.40, 0.25))
        _draw_label_value(page, boxes["name"], "HOLDER", values["name"], mutation=mutation, field="name")
        _draw_label_value(page, boxes["identifier"], "DOCUMENT NUMBER", values["identifier"], mutation=mutation, field="identifier")
        _draw_label_value(page, boxes["date"], "EXPIRY", values["date"], mutation=mutation, field="date")
        _draw_label_value(page, boxes["address"], "OBSERVATION", values["address"], mutation=mutation, field="address")
        page.insert_image(boxes["qr"], stream=_qr_png(values["qr"] + ":P2"), keep_proportion=False)
        _draw_signature(page, boxes["signature"], exemplar, pasted=mutation == "signature_paste")
        boxes["seal"] = _draw_seal(page, boxes["seal"], displaced=mutation == "seal_displacement")
        return boxes
    return _draw_identity(page, values, exemplar, mutation)


def _draw_visa(page: fitz.Page, values: dict[str, str], exemplar: str, mutation: str | None) -> dict[str, fitz.Rect]:
    boxes = _draw_identity(page, values, exemplar, mutation)
    page.draw_rect(fitz.Rect(62, 712, 533, 750), color=(0.04, 0.13, 0.24), fill=(0.04, 0.13, 0.24), width=0)
    mrz = f"V<DVSYN<{values['name'].replace(' ', '<')}<<{values['identifier'].replace('-', '')}"
    page.insert_text((72, 735), mrz[:58], fontsize=8.5, fontname="cour", color=(1, 1, 1))
    boxes["mrz"] = fitz.Rect(68, 716, 530, 744)
    return boxes


def _page_regions(boxes: dict[str, fitz.Rect], page_number: int) -> PageRegions:
    fixed_rect = fitz.Rect(30, 30, 565, 766)
    fixed = (_region(f"p{page_number}.fixed-layout", page_number, fixed_rect, "Stable synthetic template structure"),)
    variable_names = (
        "name",
        "identifier",
        "address",
        "date",
        "grade",
        "mark",
        "table_grade",
        "amount",
        "serial",
        "photo",
        "qr",
        "signature",
        "mrz",
    )
    variable = tuple(
        _region(f"p{page_number}.variable-{name}", page_number, boxes[name], f"Variable {name} field")
        for name in variable_names
        if name in boxes
    )
    security: dict[str, tuple[dict[str, Any], ...]] = {}
    for key in ("logo", "seal", "photo", "signature", "qr", "barcode", "handwriting", "mrz", "document_number", "hologram"):
        if key in boxes:
            security[key] = (_region(f"p{page_number}.security-{key}", page_number, boxes[key], f"Synthetic {key} region"),)
        elif key == "logo":
            security[key] = (_region(f"p{page_number}.security-logo", page_number, fitz.Rect(46, 48, 106, 108), "Synthetic emblem region"),)
        elif key == "document_number" and "identifier" in boxes:
            security[key] = (_region(f"p{page_number}.security-document-number", page_number, boxes["identifier"], "Synthetic document number"),)
        else:
            security[key] = ()
    return PageRegions(fixed, variable, security, {name: _box(rect) for name, rect in boxes.items()})


def _build_document(path: Path, spec: VisualSpec, exemplar: str, mutation: str | None = None) -> list[PageRegions]:
    values = VALUES[exemplar]
    document = fitz.open()
    pages: list[PageRegions] = []
    for page_number in range(1, spec.pages + 1):
        page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        _draw_common(page, spec, page_number)
        active_mutation = mutation if page_number == 1 else None
        if spec.layout == "academic":
            boxes = _draw_academic(page, values, exemplar, active_mutation)
        elif spec.layout == "certificate":
            boxes = _draw_certificate(page, values, exemplar, active_mutation)
        elif spec.layout == "identity":
            boxes = (
                _draw_identity(page, values, exemplar, active_mutation)
                if page_number == 1
                else _draw_identity_back(page, values, exemplar)
            )
        elif spec.layout == "receipt":
            boxes = _draw_receipt(page, values, exemplar, active_mutation)
        elif spec.layout == "civil":
            boxes = _draw_civil_or_letter(page, values, exemplar, active_mutation, letter=False)
        elif spec.layout == "letter":
            boxes = _draw_civil_or_letter(page, values, exemplar, active_mutation, letter=True)
        elif spec.layout == "passport":
            boxes = _draw_passport_page(page, values, exemplar, active_mutation, page_number)
        elif spec.layout == "visa":
            boxes = _draw_visa(page, values, exemplar, active_mutation)
        else:  # pragma: no cover - the static mapping is exhaustive
            raise ValueError(f"unsupported visual layout: {spec.layout}")
        pages.append(_page_regions(boxes, page_number))
    document.set_metadata({**FIXED_METADATA, "title": spec.display_name})
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path, garbage=4, deflate=True, clean=True, no_new_id=True)
    document.close()
    return pages


def _render_pages(pdf: Path, destination: Path) -> list[Path]:
    output: list[Path] = []
    destination.mkdir(parents=True, exist_ok=True)
    with fitz.open(pdf) as document:
        for index, page in enumerate(document, start=1):
            scale = RENDER_LONGEST / max(page.rect.width, page.rect.height)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, colorspace=fitz.csRGB)
            path = destination / f"page-{index:02d}.png"
            pixmap.save(path)
            output.append(path)
    return output


def _thumbnail(source: Path, destination: Path) -> tuple[int, int]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image = image.convert("RGB")
        image.thumbnail((THUMBNAIL_LONGEST, THUMBNAIL_LONGEST), Image.Resampling.LANCZOS)
        image.save(destination, "WEBP", quality=82, method=6, exact=True)
        return image.width, image.height


def _write_mask(path: Path, mask: np.ndarray) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", mask, [cv2.IMWRITE_PNG_COMPRESSION, 9])
    if not ok:
        raise RuntimeError(f"could not encode visual mask: {path}")
    path.write_bytes(encoded.tobytes())
    return {
        "relative_path": path.relative_to(PROJECT_ROOT).as_posix(),
        "mime_type": "image/png",
        "sha256": _sha256(path),
        "width": int(mask.shape[1]),
        "height": int(mask.shape[0]),
    }


def _write_shared_mask(mask: np.ndarray) -> dict[str, Any]:
    ok, encoded = cv2.imencode(".png", mask, [cv2.IMWRITE_PNG_COMPRESSION, 9])
    if not ok:
        raise RuntimeError("could not encode shared visual mask")
    content = encoded.tobytes()
    digest = hashlib.sha256(content).hexdigest()
    path = ASSET_ROOT / "_shared" / "masks" / f"{digest}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != content:
        raise RuntimeError("content-addressed shared mask contains unexpected bytes")
    if not path.exists():
        path.write_bytes(content)
    return {
        "relative_path": path.relative_to(PROJECT_ROOT).as_posix(),
        "mime_type": "image/png",
        "sha256": digest,
        "width": int(mask.shape[1]),
        "height": int(mask.shape[0]),
    }


def _descriptor(path: Path, mime_type: str, width: int, height: int) -> dict[str, Any]:
    return {
        "relative_path": path.relative_to(PROJECT_ROOT).as_posix(),
        "mime_type": mime_type,
        "sha256": _sha256(path),
        "width": width,
        "height": height,
    }


def _mutations(layout: str) -> tuple[tuple[str, str, str, str], ...]:
    if layout in {"identity", "passport", "visa"}:
        return (
            ("font_weight_change", "name", "variable_typography_change", "medium"),
            ("position_shift", "identifier", "field_displacement", "medium"),
            ("photo_paste", "photo", "photo_compositing", "high"),
            ("qr_displacement", "qr", "security_element_displacement", "high"),
            ("pasted_background", "address", "background_compositing", "high"),
        )
    if layout == "academic":
        return (
            ("font_weight_change", "name", "variable_typography_change", "medium"),
            ("baseline_shift", "identifier", "baseline_displacement", "medium"),
            ("pasted_background", "grade", "background_compositing", "high"),
            ("logical_value_change", "grade", "logical_value_inconsistency", "high"),
            ("signature_paste", "signature", "signature_compositing", "high"),
        )
    if layout == "receipt":
        return (
            ("font_weight_change", "name", "variable_typography_change", "medium"),
            ("pasted_background", "amount", "background_compositing", "high"),
            ("logical_value_change", "amount", "total_inconsistency", "high"),
            ("qr_replacement", "qr", "qr_payload_change", "high"),
            ("seal_displacement", "seal", "seal_displacement", "high"),
        )
    return (
        ("font_weight_change", "name", "variable_typography_change", "medium"),
        ("baseline_shift", "date", "baseline_displacement", "medium"),
        ("signature_paste", "signature", "signature_compositing", "high"),
        ("seal_displacement", "seal", "seal_displacement", "high"),
        ("border_interrupt", "border", "border_interruption", "high"),
    )


def _profile_from_spec(source: dict[str, Any], spec: VisualSpec, assets: list[dict[str, Any]], pages: list[PageRegions]) -> dict[str, Any]:
    profile = json.loads(json.dumps(source))
    profile["profile_id"] = spec.profile_id
    profile["display_name"] = spec.display_name
    profile["issuer"] = {
        "id": f"synthetic.{spec.slug}",
        "name": spec.issuer,
        "aliases": [spec.issuer.split()[0], "DocuVerify synthetic demonstration"],
    }
    profile["version"] = f"synthetic-visual-{GENERATOR_VERSION}"
    profile["validity"] = {"from_year": 2042, "to_year": 2042, "notes": "Fictional detector-evaluation profile only."}
    profile["expected_pages"] = {
        "minimum": spec.pages,
        "maximum": spec.pages,
        "orientation": "portrait",
        "dimensions": [{"width": PAGE_WIDTH, "height": PAGE_HEIGHT, "unit": "points", "tolerance": 0.03}],
    }
    profile["stable_headings"] = [spec.issuer, spec.title, "SYNTHETIC DEMONSTRATION"]
    for rule in profile.get("logical_rules", []):
        if rule.get("type") == "fixed_text_present":
            rule["parameters"] = {
                "texts": [spec.issuer, spec.title, "SYNTHETIC DEMONSTRATION"]
            }
    profile["keywords"] = list(dict.fromkeys([spec.slug.replace("-", " "), "synthetic demonstration", spec.title.casefold(), *profile.get("keywords", [])]))
    profile["regions"] = {
        "fixed": [region for page in pages for region in page.fixed],
        "variable": [region for page in pages for region in page.variable],
        "unknown": [],
    }
    profile["security_regions"] = {
        key: [region for page in pages for region in page.security.get(key, ())]
        for key in ("logo", "seal", "photo", "signature", "handwriting", "qr", "barcode")
    }
    profile["layout_anchors"] = [
        {
            "anchor_id": f"p{index}.synthetic-title",
            "text": spec.title,
            "page": index,
            "box": _box(fitz.Rect(118, 84, 540, 112)),
            "tolerance": 0.08,
        }
        for index in range(1, spec.pages + 1)
    ]
    profile["codes"] = {
        "qr_expectation": "optional",
        "barcode_expectation": "not_expected",
        "payload_format": "text",
        "required_keys": [],
        "issuer_prefixes": ["DV:SYNTHETIC"],
        "cryptographic_specification": None,
    }
    profile["digital_signature"] = {
        "expectation": "optional",
        "trusted_issuer_ids": [],
        "allowed_formats": ["pdf"],
    }
    profile["source"] = {
        "record_id": f"docuverify-visual-library:{spec.slug}:{GENERATOR_VERSION}",
        "authoritative_url": None,
        "retrieved_at": RETRIEVAL_DATE,
        "sha256": None,
        "format": "synthetic",
        "redistribution_status": "permitted",
        "licence": "Generated fictional DocuVerify fixture; redistribution permitted with synthetic notice retained.",
    }
    profile["provenance"] = {
        "kind": "synthetic_showcase",
        "assurance": "P0",
        "description": "Deterministically generated fictional visual reference for DocuVerify demonstrations and detector evaluation.",
    }
    profile["profile_confidence"] = 98
    profile["completeness"] = 96
    profile["known_limitations"] = [
        "This profile is fictional and is not an issuer-authenticated specimen.",
        "It may influence visual tampering risk only for candidates carrying the controlled synthetic demonstration marker.",
        "A match is retrieval evidence and never an authenticity probability.",
    ]
    profile["capability_tier"] = "visual_reference"
    profile["reference_assets"] = assets
    profile["visual_reference"] = None
    profile["enabled"] = True
    return profile


def _asset_record(
    *,
    spec: VisualSpec,
    exemplar: str,
    document_page: int,
    image_path: Path,
    thumbnail_path: Path,
    page_regions: PageRegions,
    mask_descriptors: dict[str, dict[str, Any]],
    fingerprint_path: Path,
    fingerprint: dict[str, Any],
    document_family: str,
) -> dict[str, Any]:
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"could not inspect generated page: {image_path}")
    height, width = image.shape[:2]
    with Image.open(thumbnail_path) as thumbnail:
        thumb_width, thumb_height = thumbnail.size
    side = "front" if document_page == 1 else "back"
    return {
        "asset_id": f"visual.{exemplar}.p{document_page:02d}",
        "profile_id": spec.profile_id,
        "exemplar_id": exemplar,
        "document_page_number": document_page,
        "asset_page_number": 1,
        "side": side,
        "relative_path": image_path.relative_to(PROJECT_ROOT).as_posix(),
        "mime_type": "image/png",
        "sha256": _sha256(image_path),
        "dimensions": {"width": width, "height": height, "unit": "pixels"},
        "source_class": "synthetic_demo",
        "trust_level": "P0",
        "issuer": spec.issuer,
        "document_family": document_family,
        "profile_version": f"synthetic-visual-{GENERATOR_VERSION}",
        "languages": ["en"],
        "creation_method": "Deterministic PyMuPDF vector generation and bounded PNG rendering.",
        "source_url": None,
        "retrieval_date": RETRIEVAL_DATE,
        "redistribution_status": "permitted",
        "licence_status_note": "Fictional DocuVerify demonstration asset; synthetic notice must remain visible.",
        "may_influence_tampering_risk": True,
        "demonstration_only": True,
        "enabled": True,
        "thumbnail": _descriptor(thumbnail_path, "image/webp", thumb_width, thumb_height),
        "pixel_masks": mask_descriptors,
        "fingerprint_file": {
            "relative_path": fingerprint_path.relative_to(PROJECT_ROOT).as_posix(),
            "mime_type": "application/json",
            "sha256": _sha256(fingerprint_path),
        },
        "fixed_region_masks": list(page_regions.fixed),
        "variable_region_masks": list(page_regions.variable),
        "security_element_regions": {key: list(values) for key, values in page_regions.security.items()},
        "precomputed_fingerprint": fingerprint,
    }


def generate() -> dict[str, Any]:
    _safe_reset(ASSET_ROOT)
    _safe_reset(EVALUATION_ROOT)
    catalog = json.loads(PROFILE_CATALOG.read_text(encoding="utf-8"))
    sources = {profile["profile_id"]: profile for profile in catalog}
    generated_profiles: list[dict[str, Any]] = []
    generated_assets = 0
    generated_pages = 0
    questioned_count = 0
    all_outputs: list[Path] = []

    for spec in SPECS:
        if spec.source_profile_id not in sources:
            raise KeyError(f"missing source profile: {spec.source_profile_id}")
        asset_dir = ASSET_ROOT / spec.slug
        evaluation_dir = EVALUATION_ROOT / spec.slug
        truth_dir = evaluation_dir / "truth"
        questioned_dir = evaluation_dir / "questioned"
        ground_truth_dir = evaluation_dir / "ground-truth"
        fingerprints_dir = asset_dir / "fingerprints"
        truth_dir.mkdir(parents=True, exist_ok=True)
        questioned_dir.mkdir(parents=True, exist_ok=True)
        ground_truth_dir.mkdir(parents=True, exist_ok=True)

        exemplar_pages: dict[str, list[PageRegions]] = {}
        rendered_by_exemplar: dict[str, list[Path]] = {}
        for exemplar in ("reference-a", "reference-b"):
            pdf = truth_dir / f"{exemplar}.pdf"
            exemplar_pages[exemplar] = _build_document(pdf, spec, exemplar)
            render_dir = asset_dir / exemplar
            rendered_by_exemplar[exemplar] = _render_pages(pdf, render_dir)
            all_outputs.append(pdf)

        page_regions = exemplar_pages["reference-a"]
        if exemplar_pages["reference-b"] != page_regions:
            raise RuntimeError(f"legitimate exemplar geometry changed for {spec.profile_id}")

        page_masks: dict[int, dict[str, dict[str, Any]]] = {}
        for page_number, regions in enumerate(page_regions, start=1):
            image = cv2.imread(str(rendered_by_exemplar["reference-a"][page_number - 1]), cv2.IMREAD_COLOR)
            if image is None:
                raise RuntimeError("generated reference page could not be decoded")
            fixed = region_mask_image(image.shape, regions.fixed, page_number)
            variable = region_mask_image(image.shape, regions.variable, page_number)
            security_values = tuple(region for values in regions.security.values() for region in values)
            security = region_mask_image(image.shape, security_values, page_number)
            page_masks[page_number] = {
                "fixed": _write_shared_mask(fixed),
                "variable": _write_shared_mask(variable),
                "security": _write_shared_mask(security),
            }

        reference_assets: list[dict[str, Any]] = []
        manifest_pages: list[dict[str, Any]] = []
        for exemplar in ("reference-a", "reference-b"):
            for page_number, image_path in enumerate(rendered_by_exemplar[exemplar], start=1):
                thumbnail_path = image_path.with_suffix(".thumbnail.webp")
                _thumbnail(image_path, thumbnail_path)
                image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
                if image is None:
                    raise RuntimeError(f"generated visual reference could not be decoded: {image_path}")
                digest = _sha256(image_path)
                fingerprint = compute_visual_fingerprint(
                    image,
                    fixed_regions=page_regions[page_number - 1].fixed,
                    variable_regions=page_regions[page_number - 1].variable,
                    security_regions=page_regions[page_number - 1].security,
                    page_number=page_number,
                    source_sha256=digest,
                )
                fingerprint_path = fingerprints_dir / f"{exemplar}-page-{page_number:02d}.json"
                _write_json(fingerprint_path, fingerprint)
                asset = _asset_record(
                    spec=spec,
                    exemplar=exemplar,
                    document_page=page_number,
                    image_path=image_path,
                    thumbnail_path=thumbnail_path,
                    page_regions=page_regions[page_number - 1],
                    mask_descriptors=page_masks[page_number],
                    fingerprint_path=fingerprint_path,
                    fingerprint=fingerprint,
                    document_family=str(sources[spec.source_profile_id]["document_family"]),
                )
                reference_assets.append(asset)
                manifest_pages.append({"exemplar_id": exemplar, "document_page_number": page_number, "asset": asset})
                generated_assets += 1
                generated_pages += 1

        evaluations: list[dict[str, Any]] = []
        for mutation, field, category, severity in _mutations(spec.layout):
            output = questioned_dir / f"b-{mutation.replace('_', '-')}.pdf"
            mutated_pages = _build_document(output, spec, "reference-b", mutation)
            named = mutated_pages[0].named_boxes
            expected_box = named.get(field) or named["border"]
            evaluations.append(
                {
                    "file": output.relative_to(EVALUATION_ROOT).as_posix(),
                    "sha256": _sha256(output),
                    "deterministic_seed": int(hashlib.sha256(f"{spec.profile_id}:{mutation}".encode()).hexdigest()[:8], 16),
                    "changed_field": field,
                    "mutation": mutation,
                    "expected_category": category,
                    "expected_severity": severity,
                    "page_number": 1,
                    "expected_normalized_box": expected_box,
                    "human_readable_ground_truth": f"Reference B was regenerated with the document-appropriate {mutation.replace('_', ' ')} in the {field} region.",
                    "verification_mode": "docuvault",
                    "production_access_permitted": False,
                }
            )
            questioned_count += 1
            all_outputs.append(output)

        evaluation_manifest = {
            "schema_version": "1.0.0",
            "synthetic": True,
            "profile_id": spec.profile_id,
            "source_profile_id": spec.source_profile_id,
            "document_family": sources[spec.source_profile_id]["document_family"],
            "display_family": spec.display_name,
            "generator_version": GENERATOR_VERSION,
            "truth": {
                "reference_a": "truth/reference-a.pdf",
                "reference_b": "truth/reference-b.pdf",
                "reference_a_sha256": _sha256(truth_dir / "reference-a.pdf"),
                "reference_b_sha256": _sha256(truth_dir / "reference-b.pdf"),
            },
            "questioned": evaluations,
            "notice": "All documents, identities, issuers, identifiers, signatures, seals, and payloads are fictional.",
        }
        _write_json(ground_truth_dir / "manifest.json", evaluation_manifest)

        asset_manifest = {
            "schema_version": "1.0.0",
            "profile_id": spec.profile_id,
            "source_profile_id": spec.source_profile_id,
            "profile_version": GENERATOR_VERSION,
            "issuer": spec.issuer,
            "document_family": sources[spec.source_profile_id]["document_family"],
            "display_family": spec.display_name,
            "source_class": "synthetic_demo",
            "trust_level": "P0",
            "creation_method": "Deterministic PyMuPDF vector generation and bounded PNG rendering.",
            "source_url": None,
            "retrieval_date": RETRIEVAL_DATE,
            "redistribution_status": "permitted",
            "licence_status_note": "Fictional DocuVerify demonstration library; retain the synthetic notice.",
            "may_influence_tampering_risk": True,
            "demonstration_only": True,
            "pages": manifest_pages,
        }
        _write_json(asset_dir / "manifest.json", asset_manifest)
        generated_profiles.append(
            _profile_from_spec(sources[spec.source_profile_id], spec, reference_assets, page_regions)
        )

    # Keep the existing Lumen profile ID in the core catalog and place only the
    # 19 new companion profiles in the generated companion catalog.
    generated_by_id = {profile["profile_id"]: profile for profile in generated_profiles}
    lumen_id = "synthetic.lumen-grove.achievement-record.v1"
    updated_core = [generated_by_id[lumen_id] if profile["profile_id"] == lumen_id else profile for profile in catalog]
    companions = sorted((profile for profile in generated_profiles if profile["profile_id"] != lumen_id), key=lambda item: item["profile_id"])
    _write_json(PROFILE_CATALOG, updated_core)
    _write_json(SYNTHETIC_PROFILE_CATALOG, companions)

    summary = {
        "generator_version": GENERATOR_VERSION,
        "existing_profiles": len(catalog),
        "synthetic_visual_profiles": len(generated_profiles),
        "synthetic_companion_profiles": len(companions),
        "visual_assets": generated_assets,
        "rendered_pages": generated_pages,
        "evaluation_folders": len(SPECS),
        "questioned_documents": questioned_count,
        "pdf_outputs": len(all_outputs),
    }
    _write_json(ASSET_ROOT / "library-summary.json", summary)
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Report whether generated roots exist without rewriting them.")
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    if arguments.check:
        ready = ASSET_ROOT.is_dir() and EVALUATION_ROOT.is_dir() and SYNTHETIC_PROFILE_CATALOG.is_file()
        print(json.dumps({"ready": ready, "asset_root": str(ASSET_ROOT), "evaluation_root": str(EVALUATION_ROOT)}))
        return 0 if ready else 1
    print(json.dumps(generate(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
