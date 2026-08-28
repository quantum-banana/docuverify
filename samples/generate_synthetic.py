"""Generate deterministic, fictional golden documents for the real demo pipeline.

Run from the repository root with:
    python samples/generate_synthetic.py
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import cv2
import fitz
import numpy as np


ROOT = Path(__file__).resolve().parent
SYNTHETIC_DIR = ROOT / "synthetic"
EXPECTED_DIR = ROOT / "expected"
PAGE_WIDTH = 595.0
PAGE_HEIGHT = 842.0
RENDER_LONGEST = 1800
RESULT_BBOX = {"x": 0.46, "y": 0.475, "width": 0.38, "height": 0.065}


def build_certificate(path: Path, result_value: str) -> None:
    document = fitz.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.draw_rect(page.rect, color=(0.08, 0.16, 0.24), fill=(0.97, 0.98, 0.985), width=0)
    page.draw_rect(fitz.Rect(0, 0, PAGE_WIDTH, 128), color=None, fill=(0.045, 0.11, 0.17))
    page.draw_rect(fitz.Rect(0, 128, PAGE_WIDTH, 135), color=None, fill=(0.10, 0.68, 0.68))

    # A deliberately original geometric mark—not a copied institution logo.
    center = fitz.Point(76, 64)
    radius = 31
    points = []
    for x_factor, y_factor in ((0, -1), (0.87, -0.5), (0.87, 0.5), (0, 1), (-0.87, 0.5), (-0.87, -0.5)):
        points.append(fitz.Point(center.x + radius * x_factor, center.y + radius * y_factor))
    shape = page.new_shape()
    for start, end in zip(points, points[1:] + points[:1]):
        shape.draw_line(start, end)
    shape.finish(color=(0.15, 0.78, 0.78), fill=(0.06, 0.19, 0.26), width=2)
    shape.commit()
    page.insert_text((58, 71), "DV", fontsize=19, fontname="hebo", color=(0.82, 1.0, 1.0))
    page.insert_text((126, 53), "NORTHSTAR SYNTHETIC ACADEMY", fontsize=18, fontname="hebo", color=(0.93, 0.98, 1.0))
    page.insert_text((126, 78), "DEMONSTRATION RECORD • FICTIONAL", fontsize=9, fontname="helv", color=(0.55, 0.82, 0.84))

    page.insert_text((62, 188), "CERTIFICATE OF COMPLETION", fontsize=24, fontname="hebo", color=(0.06, 0.16, 0.23))
    page.insert_text((63, 215), "This synthetic record is issued solely to demonstrate document verification.", fontsize=10, fontname="helv", color=(0.30, 0.38, 0.43))
    page.draw_line(fitz.Point(63, 235), fitz.Point(532, 235), color=(0.69, 0.75, 0.78), width=0.8)

    rows = (
        (268, "RECIPIENT", "ARIA NOVA"),
        (330, "PROGRAM", "APPLIED SYSTEMS VERIFICATION"),
        (392, "RESULT", result_value),
        (454, "CERTIFICATE ID", "SYN-2042-017"),
        (516, "ISSUE DATE", "17 JUNE 2042"),
    )
    for top, label, value in rows:
        rect = fitz.Rect(62, top, 533, top + 48)
        page.draw_rect(rect, color=(0.80, 0.84, 0.86), fill=(1, 1, 1), width=0.7, radius=0.10)
        page.insert_text((80, top + 19), label, fontsize=8, fontname="hebo", color=(0.28, 0.48, 0.52))
        page.insert_text((286, top + 29), value, fontsize=14, fontname="hebo", color=(0.07, 0.14, 0.19))

    page.draw_line(fitz.Point(80, 650), fitz.Point(250, 650), color=(0.27, 0.34, 0.38), width=0.8)
    page.insert_text((80, 670), "MIRA QUILL • SYNTHETIC REGISTRAR", fontsize=8, fontname="helv", color=(0.35, 0.42, 0.46))
    seal_center = fitz.Point(460, 650)
    page.draw_circle(seal_center, 48, color=(0.08, 0.53, 0.55), fill=(0.91, 0.98, 0.98), width=2)
    page.draw_circle(seal_center, 38, color=(0.08, 0.53, 0.55), width=0.8)
    page.insert_text((438, 646), "2042", fontsize=13, fontname="hebo", color=(0.06, 0.40, 0.42))
    page.insert_text((431, 662), "SYNTHETIC", fontsize=7, fontname="helv", color=(0.06, 0.40, 0.42))

    page.draw_rect(fitz.Rect(0, 764, PAGE_WIDTH, PAGE_HEIGHT), color=None, fill=(0.91, 0.94, 0.95))
    page.insert_text((62, 793), "SYNTHETIC DEMONSTRATION • NOT A REAL CREDENTIAL", fontsize=10, fontname="hebo", color=(0.52, 0.20, 0.17))
    page.insert_text((62, 814), "All names, identifiers, marks, and institutions on this page are fictional.", fontsize=8, fontname="helv", color=(0.38, 0.45, 0.49))
    document.set_metadata(
        {
            "title": "DocuVerify Synthetic Certificate",
            "author": "DocuVerify Fixture Generator",
            "subject": "Fictional test fixture; not a real credential",
            "keywords": "synthetic, fixture, docuverify",
            "creator": "DocuVerify deterministic generator",
            "producer": "PyMuPDF",
            "creationDate": "D:20420101000000Z",
            "modDate": "D:20420101000000Z",
        }
    )
    document.save(path, garbage=4, deflate=True, clean=True, no_new_id=True)
    document.close()


def render_pdf(pdf_path: Path, png_path: Path) -> tuple[int, int]:
    with fitz.open(pdf_path) as document:
        page = document[0]
        scale = RENDER_LONGEST / max(page.rect.width, page.rect.height)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, colorspace=fitz.csRGB)
        pixmap.save(png_path)
        return pixmap.width, pixmap.height


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    SYNTHETIC_DIR.mkdir(parents=True, exist_ok=True)
    EXPECTED_DIR.mkdir(parents=True, exist_ok=True)
    reference_pdf = SYNTHETIC_DIR / "reference.pdf"
    clean_pdf = SYNTHETIC_DIR / "clean_candidate.pdf"
    tampered_pdf = SYNTHETIC_DIR / "tampered_candidate.pdf"
    build_certificate(reference_pdf, "DISTINCTION")
    shutil.copyfile(reference_pdf, clean_pdf)
    build_certificate(tampered_pdf, "PASS")
    reference_size = render_pdf(reference_pdf, SYNTHETIC_DIR / "reference.png")
    clean_size = render_pdf(clean_pdf, SYNTHETIC_DIR / "clean_candidate.png")
    tampered_size = render_pdf(tampered_pdf, SYNTHETIC_DIR / "tampered_candidate.png")
    if reference_size != clean_size or reference_size != tampered_size:
        raise RuntimeError("Synthetic pages must have identical rendered dimensions")

    width, height = reference_size
    mask = np.zeros((height, width), dtype=np.uint8)
    x0 = round(RESULT_BBOX["x"] * width)
    y0 = round(RESULT_BBOX["y"] * height)
    x1 = round((RESULT_BBOX["x"] + RESULT_BBOX["width"]) * width)
    y1 = round((RESULT_BBOX["y"] + RESULT_BBOX["height"]) * height)
    mask[y0:y1, x0:x1] = 255
    success, encoded = cv2.imencode(".png", mask, [cv2.IMWRITE_PNG_COMPRESSION, 9])
    if not success:
        raise RuntimeError("Could not encode expected mask")
    (EXPECTED_DIR / "tamper_mask.png").write_bytes(encoded.tobytes())

    manifest = {
        "schema_version": "1.0",
        "fixture": "northstar_synthetic_certificate",
        "synthetic": True,
        "notice": "Fictional demonstration data; not a real credential or institution.",
        "page_count": 1,
        "rendered_width": width,
        "rendered_height": height,
        "alterations": [
            {
                "field": "RESULT",
                "before": "DISTINCTION",
                "after": "PASS",
                "normalized_bbox": RESULT_BBOX,
                "expected_category": "text_content_change",
            }
        ],
        "thresholds": {
            "clean_max_risk": 15,
            "tampered_min_risk": 50,
            "minimum_localization_iou": 0.05,
        },
        "files": {
            name: {"sha256": sha256(SYNTHETIC_DIR / name)}
            for name in (
                "reference.pdf",
                "clean_candidate.pdf",
                "tampered_candidate.pdf",
                "reference.png",
                "clean_candidate.png",
                "tampered_candidate.png",
            )
        },
    }
    (EXPECTED_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
