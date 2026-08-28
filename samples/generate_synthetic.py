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
MULTIPAGE_RESULT_RECT = fitz.Rect(306, 378, 526, 432)
TEMPLATE_MANIPULATED_RECT = fitz.Rect(252, 446, 526, 505)

FIXED_METADATA = {
    "author": "DocuVerify Fixture Generator",
    "subject": "Fictional test fixture; not a real credential",
    "keywords": "synthetic, fixture, docuverify",
    "creator": "DocuVerify deterministic generator",
    "producer": "PyMuPDF",
    "creationDate": "D:20420101000000Z",
    "modDate": "D:20420101000000Z",
}


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


def _save_document(document: fitz.Document, path: Path, title: str) -> None:
    document.set_metadata({**FIXED_METADATA, "title": title})
    document.save(path, garbage=4, deflate=True, clean=True, no_new_id=True)
    document.close()


def _normalized_rect(rect: fitz.Rect) -> dict[str, float]:
    return {
        "x": round(rect.x0 / PAGE_WIDTH, 6),
        "y": round(rect.y0 / PAGE_HEIGHT, 6),
        "width": round(rect.width / PAGE_WIDTH, 6),
        "height": round(rect.height / PAGE_HEIGHT, 6),
    }


def _draw_fixture_mark(page: fitz.Page, x: float = 58, y: float = 54) -> None:
    center = fitz.Point(x + 22, y + 22)
    page.draw_circle(center, 22, color=(0.10, 0.72, 0.70), fill=(0.06, 0.18, 0.25), width=1.6)
    page.insert_text((x + 9, y + 29), "DV", fontsize=13, fontname="hebo", color=(0.86, 1, 1))


def _draw_page_frame(
    page: fitz.Page,
    *,
    heading: str,
    subtitle: str,
    page_code: str,
) -> None:
    page.draw_rect(page.rect, color=None, fill=(0.97, 0.98, 0.985), width=0)
    page.draw_rect(fitz.Rect(0, 0, PAGE_WIDTH, 112), color=None, fill=(0.045, 0.11, 0.17))
    page.draw_rect(fitz.Rect(0, 112, PAGE_WIDTH, 118), color=None, fill=(0.10, 0.68, 0.68))
    _draw_fixture_mark(page)
    page.insert_text((118, 52), heading, fontsize=18, fontname="hebo", color=(0.94, 0.98, 1))
    page.insert_text((118, 76), subtitle, fontsize=9, fontname="helv", color=(0.60, 0.82, 0.84))
    page.draw_rect(fitz.Rect(0, 762, PAGE_WIDTH, PAGE_HEIGHT), color=None, fill=(0.91, 0.94, 0.95))
    page.insert_text((54, 791), "SYNTHETIC FIXTURE - NOT A REAL CREDENTIAL", fontsize=9, fontname="hebo", color=(0.50, 0.19, 0.16))
    page.insert_text((54, 812), "All institutions, people, identifiers, and results are fictional.", fontsize=8, fontname="helv", color=(0.37, 0.44, 0.48))
    page.insert_text((493, 812), page_code, fontsize=8, fontname="hebo", color=(0.28, 0.47, 0.50))


def _draw_info_row(
    page: fitz.Page,
    *,
    top: float,
    label: str,
    value: str,
    value_x: float = 280,
    value_font: str = "hebo",
    value_size: float = 14,
    value_color: tuple[float, float, float] = (0.07, 0.14, 0.19),
    fill: tuple[float, float, float] = (1, 1, 1),
    border: tuple[float, float, float] = (0.80, 0.84, 0.86),
    border_width: float = 0.7,
) -> None:
    page.draw_rect(
        fitz.Rect(62, top, 533, top + 52),
        color=border,
        fill=fill,
        width=border_width,
        radius=0.10,
    )
    page.insert_text((80, top + 21), label, fontsize=8, fontname="hebo", color=(0.28, 0.48, 0.52))
    page.insert_text(
        (value_x, top + 32),
        value,
        fontsize=value_size,
        fontname=value_font,
        color=value_color,
    )


def _draw_overview_page(page: fitz.Page) -> None:
    _draw_page_frame(
        page,
        heading="NORTHSTAR SYNTHETIC TRANSCRIPT",
        subtitle="FICTIONAL MULTI-PAGE ANALYSIS RECORD",
        page_code="RECORD 1",
    )
    page.insert_text((62, 170), "RECORD OVERVIEW", fontsize=24, fontname="hebo", color=(0.06, 0.16, 0.23))
    page.insert_text((63, 198), "A deterministic record created solely for local verification testing.", fontsize=10, fontname="helv", color=(0.30, 0.38, 0.43))
    _draw_info_row(page, top=244, label="RECIPIENT", value="ARIA NOVA")
    _draw_info_row(page, top=314, label="RECORD ID", value="NSR-2042-031")
    _draw_info_row(page, top=384, label="PROGRAM", value="APPLIED SYSTEMS")
    _draw_info_row(page, top=454, label="PERIOD", value="2041-2042")
    page.draw_rect(fitz.Rect(62, 555, 533, 690), color=(0.77, 0.84, 0.86), fill=(0.93, 0.98, 0.98), width=0.8)
    page.insert_text((82, 588), "DOCUMENT PURPOSE", fontsize=9, fontname="hebo", color=(0.12, 0.47, 0.49))
    page.insert_textbox(
        fitz.Rect(82, 606, 510, 670),
        "This page establishes fictional identity and program context. Page 2 contains module results. Page 3 contains the synthetic completion summary.",
        fontsize=10,
        fontname="helv",
        color=(0.22, 0.31, 0.35),
        lineheight=1.45,
    )


def _draw_results_page(page: fitz.Page, result_value: str) -> None:
    _draw_page_frame(
        page,
        heading="NORTHSTAR SYNTHETIC TRANSCRIPT",
        subtitle="FICTIONAL MULTI-PAGE ANALYSIS RECORD",
        page_code="RECORD 2",
    )
    page.insert_text((62, 170), "MODULE RESULTS", fontsize=24, fontname="hebo", color=(0.06, 0.16, 0.23))
    page.insert_text((63, 198), "Deterministic values for exact-comparison and localization tests.", fontsize=10, fontname="helv", color=(0.30, 0.38, 0.43))
    _draw_info_row(page, top=238, label="FOUNDATIONS", value="88", value_x=330)
    _draw_info_row(page, top=308, label="SYSTEMS LAB", value="91", value_x=330)
    _draw_info_row(page, top=378, label="VERIFICATION", value=result_value, value_x=330)
    _draw_info_row(page, top=448, label="AGGREGATE", value="90", value_x=330)
    page.draw_line(fitz.Point(62, 548), fitz.Point(533, 548), color=(0.69, 0.75, 0.78), width=0.8)
    page.insert_text((62, 580), "ASSESSMENT NOTE", fontsize=9, fontname="hebo", color=(0.28, 0.48, 0.52))
    page.insert_textbox(
        fitz.Rect(62, 600, 533, 670),
        "Scores and classifications on this page are invented. They are not associated with a real learner, institution, or assessment.",
        fontsize=10,
        fontname="helv",
        color=(0.28, 0.35, 0.39),
        lineheight=1.45,
    )


def _draw_completion_page(page: fitz.Page) -> None:
    _draw_page_frame(
        page,
        heading="NORTHSTAR SYNTHETIC TRANSCRIPT",
        subtitle="FICTIONAL MULTI-PAGE ANALYSIS RECORD",
        page_code="RECORD 3",
    )
    page.insert_text((62, 170), "COMPLETION SUMMARY", fontsize=24, fontname="hebo", color=(0.06, 0.16, 0.23))
    page.insert_text((63, 198), "Closing page for deterministic page-order and correspondence tests.", fontsize=10, fontname="helv", color=(0.30, 0.38, 0.43))
    page.draw_rect(fitz.Rect(62, 250, 533, 405), color=(0.75, 0.84, 0.86), fill=(0.92, 0.98, 0.98), width=1.0)
    page.insert_text((90, 294), "SYNTHETIC COMPLETION CONFIRMED", fontsize=18, fontname="hebo", color=(0.07, 0.39, 0.42))
    page.insert_text((90, 326), "Record code: NSR-2042-031", fontsize=11, fontname="helv", color=(0.23, 0.34, 0.38))
    page.insert_text((90, 352), "Completion date: 17 JUNE 2042", fontsize=11, fontname="helv", color=(0.23, 0.34, 0.38))
    seal_center = fitz.Point(452, 520)
    page.draw_circle(seal_center, 60, color=(0.08, 0.53, 0.55), fill=(0.90, 0.98, 0.98), width=2)
    page.draw_circle(seal_center, 47, color=(0.08, 0.53, 0.55), width=0.8)
    page.insert_text((425, 516), "2042", fontsize=15, fontname="hebo", color=(0.06, 0.40, 0.42))
    page.insert_text((418, 537), "FICTIONAL", fontsize=8, fontname="helv", color=(0.06, 0.40, 0.42))
    page.draw_line(fitz.Point(76, 560), fitz.Point(265, 560), color=(0.27, 0.34, 0.38), width=0.8)
    page.insert_text((76, 582), "MIRA QUILL - SYNTHETIC REGISTRAR", fontsize=8, fontname="helv", color=(0.35, 0.42, 0.46))


def _draw_added_page(page: fitz.Page) -> None:
    _draw_page_frame(
        page,
        heading="NORTHSTAR SYNTHETIC TRANSCRIPT",
        subtitle="UNMATCHED FICTIONAL APPENDIX",
        page_code="RECORD 4",
    )
    page.insert_text((62, 170), "ADDED REVIEW NOTE", fontsize=24, fontname="hebo", color=(0.06, 0.16, 0.23))
    page.insert_text((63, 198), "This deliberately unmatched page exercises added-page detection.", fontsize=10, fontname="helv", color=(0.30, 0.38, 0.43))
    page.draw_rect(fitz.Rect(62, 260, 533, 455), color=(0.83, 0.66, 0.30), fill=(1.0, 0.97, 0.88), width=1.1)
    page.insert_text((86, 310), "UNMATCHED SYNTHETIC PAGE", fontsize=18, fontname="hebo", color=(0.48, 0.28, 0.08))
    page.insert_textbox(
        fitz.Rect(86, 342, 505, 424),
        "No trusted counterpart exists for this appendix. The content is fictional and intentionally obvious for deterministic testing.",
        fontsize=11,
        fontname="helv",
        color=(0.35, 0.27, 0.15),
        lineheight=1.45,
    )


def build_multipage_record(
    path: Path,
    *,
    page_sequence: tuple[str, ...] = ("overview", "results", "completion"),
    result_value: str = "DISTINCTION",
) -> None:
    drawers = {
        "overview": _draw_overview_page,
        "results": lambda page: _draw_results_page(page, result_value),
        "completion": _draw_completion_page,
        "added": _draw_added_page,
    }
    document = fitz.open()
    for page_kind in page_sequence:
        page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        drawers[page_kind](page)
    _save_document(document, path, "DocuVerify Fictional Multi-Page Record")


def _draw_template_page(
    page: fitz.Page,
    *,
    values: dict[str, str],
    manipulated: bool,
) -> None:
    _draw_page_frame(
        page,
        heading="LUMEN GROVE SYNTHETIC RECORD",
        subtitle="FICTIONAL TEMPLATE-COMPARISON FIXTURE",
        page_code="TEMPLATE 1",
    )
    page.insert_text((62, 170), "ACHIEVEMENT RECORD", fontsize=24, fontname="hebo", color=(0.06, 0.16, 0.23))
    page.insert_text((63, 198), "Labels are fixed; values are intentionally variable in template mode.", fontsize=10, fontname="helv", color=(0.30, 0.38, 0.43))
    _draw_info_row(page, top=236, label="NAME", value=values["name"], value_x=270)
    _draw_info_row(page, top=306, label="IDENTIFIER", value=values["identifier"], value_x=270)
    _draw_info_row(page, top=376, label="ISSUE DATE", value=values["date"], value_x=270)
    if manipulated:
        _draw_info_row(page, top=446, label="RESULT", value="", value_x=270)
        page.draw_rect(
            TEMPLATE_MANIPULATED_RECT,
            color=(0.72, 0.20, 0.12),
            fill=(1.0, 0.91, 0.82),
            width=1.4,
        )
        page.insert_text(
            (270, 484),
            values["result"],
            fontsize=19,
            fontname="cobo",
            color=(0.52, 0.08, 0.05),
        )
    else:
        _draw_info_row(page, top=446, label="RESULT", value=values["result"], value_x=270)
    page.draw_rect(fitz.Rect(62, 565, 533, 682), color=(0.79, 0.85, 0.87), fill=(0.94, 0.98, 0.985), width=0.8)
    page.insert_text((82, 598), "TEMPLATE RULE", fontsize=9, fontname="hebo", color=(0.16, 0.45, 0.48))
    page.insert_textbox(
        fitz.Rect(82, 618, 510, 666),
        "Values may change when their typography, baseline, spacing, and background remain consistent with the trusted template.",
        fontsize=10,
        fontname="helv",
        color=(0.25, 0.34, 0.38),
        lineheight=1.35,
    )


def build_template_record(path: Path, *, values: dict[str, str], manipulated: bool = False) -> None:
    document = fitz.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    _draw_template_page(page, values=values, manipulated=manipulated)
    _save_document(document, path, "DocuVerify Fictional Template Record")


def render_pdf_pages(pdf_path: Path, output_stem: str) -> list[Path]:
    output_paths: list[Path] = []
    with fitz.open(pdf_path) as document:
        for page_index, page in enumerate(document):
            scale = RENDER_LONGEST / max(page.rect.width, page.rect.height)
            pixmap = page.get_pixmap(
                matrix=fitz.Matrix(scale, scale),
                alpha=False,
                colorspace=fitz.csRGB,
            )
            output_path = SYNTHETIC_DIR / f"{output_stem}_page_{page_index + 1}.png"
            pixmap.save(output_path)
            output_paths.append(output_path)
    return output_paths


def _write_mask(path: Path, bbox: dict[str, float], width: int, height: int) -> None:
    mask = np.zeros((height, width), dtype=np.uint8)
    x0 = max(0, min(width, round(bbox["x"] * width)))
    y0 = max(0, min(height, round(bbox["y"] * height)))
    x1 = max(x0, min(width, round((bbox["x"] + bbox["width"]) * width)))
    y1 = max(y0, min(height, round((bbox["y"] + bbox["height"]) * height)))
    mask[y0:y1, x0:x1] = 255
    success, encoded = cv2.imencode(".png", mask, [cv2.IMWRITE_PNG_COMPRESSION, 9])
    if not success:
        raise RuntimeError(f"Could not encode expected mask {path.name}")
    path.write_bytes(encoded.tobytes())


def _draw_cv_text(
    image: np.ndarray,
    text: str,
    origin: tuple[int, int],
    *,
    scale: float,
    thickness: int,
    color: tuple[int, int, int],
    font: int = cv2.FONT_HERSHEY_SIMPLEX,
) -> tuple[int, int, int, int]:
    size, baseline = cv2.getTextSize(text, font, scale, thickness)
    cv2.putText(image, text, origin, font, scale, color, thickness, cv2.LINE_AA)
    return origin[0], origin[1] - size[1], origin[0] + size[0], origin[1] + baseline


def _normalize_pixel_box(
    bbox: tuple[int, int, int, int], width: int, height: int
) -> dict[str, float]:
    return {
        "x": round(bbox[0] / width, 6),
        "y": round(bbox[1] / height, 6),
        "width": round((bbox[2] - bbox[0]) / width, 6),
        "height": round((bbox[3] - bbox[1]) / height, 6),
    }


def build_raster_only_fixture(png_path: Path, pdf_path: Path) -> list[dict[str, object]]:
    width, height = 1272, 1800
    image = np.full((height, width, 3), (250, 249, 246), dtype=np.uint8)
    cv2.rectangle(image, (0, 0), (width, 245), (48, 31, 17), -1)
    cv2.rectangle(image, (0, 245), (width, 258), (190, 171, 28), -1)
    cv2.circle(image, (118, 120), 68, (70, 190, 188), 5, cv2.LINE_AA)
    cv2.circle(image, (118, 120), 56, (63, 47, 28), -1, cv2.LINE_AA)
    token_boxes: list[dict[str, object]] = []

    def token(
        text: str,
        origin: tuple[int, int],
        *,
        scale: float = 1.45,
        thickness: int = 3,
        color: tuple[int, int, int] = (43, 38, 32),
    ) -> None:
        bbox = _draw_cv_text(
            image,
            text,
            origin,
            scale=scale,
            thickness=thickness,
            color=color,
        )
        token_boxes.append({"text": text, "normalized_bbox": _normalize_pixel_box(bbox, width, height)})

    token("DV", (78, 143), scale=1.2, thickness=3, color=(240, 250, 250))
    token("ORBITAL", (225, 112), scale=1.55, thickness=4, color=(242, 249, 250))
    token("ARCHIVE", (570, 112), scale=1.55, thickness=4, color=(242, 249, 250))
    _draw_cv_text(image, "FICTIONAL IMAGE-ONLY RECORD", (228, 170), scale=0.78, thickness=2, color=(198, 222, 224))
    _draw_cv_text(image, "RASTER VERIFICATION SHEET", (120, 355), scale=1.45, thickness=4, color=(45, 53, 59))
    _draw_cv_text(image, "No embedded PDF text is present", (122, 405), scale=0.72, thickness=2, color=(93, 101, 106))

    rows = (
        (535, "NAME", ("NOVA", "QUILL")),
        (700, "IDENTIFIER", ("QVX-7319",)),
        (865, "DATE", ("2042-06-17",)),
        (1030, "RESULT", ("DISTINCTION",)),
    )
    for baseline, label, values in rows:
        cv2.rectangle(image, (118, baseline - 100), (1152, baseline + 60), (226, 223, 216), 2)
        _draw_cv_text(image, label, (155, baseline - 33), scale=0.72, thickness=2, color=(105, 95, 72))
        value_x = 505
        for value in values:
            token(value, (value_x, baseline + 18), scale=1.18, thickness=3)
            value_width = cv2.getTextSize(value, cv2.FONT_HERSHEY_SIMPLEX, 1.18, 3)[0][0]
            value_x += value_width + 30

    cv2.circle(image, (960, 1350), 118, (68, 152, 150), 5, cv2.LINE_AA)
    cv2.circle(image, (960, 1350), 94, (230, 246, 244), -1, cv2.LINE_AA)
    token("SYNTHETIC", (845, 1370), scale=0.72, thickness=2, color=(58, 121, 119))
    _draw_cv_text(image, "FICTIONAL FIXTURE - NOT A REAL CREDENTIAL", (120, 1640), scale=0.78, thickness=2, color=(65, 61, 57))
    _draw_cv_text(image, "All displayed names and identifiers are invented.", (120, 1690), scale=0.62, thickness=2, color=(93, 90, 86))

    success, encoded = cv2.imencode(".png", image, [cv2.IMWRITE_PNG_COMPRESSION, 9])
    if not success:
        raise RuntimeError("Could not encode raster-only fixture")
    png_bytes = encoded.tobytes()
    png_path.write_bytes(png_bytes)

    document = fitz.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_image(page.rect, stream=png_bytes, keep_proportion=False)
    _save_document(document, pdf_path, "DocuVerify Fictional Raster-Only Record")
    return token_boxes


def _generate_phase2_fixtures() -> dict[str, object]:
    multipage_reference = SYNTHETIC_DIR / "multipage_reference.pdf"
    multipage_clean = SYNTHETIC_DIR / "multipage_clean_candidate.pdf"
    multipage_tampered = SYNTHETIC_DIR / "multipage_tampered_candidate.pdf"
    multipage_missing = SYNTHETIC_DIR / "multipage_missing_candidate.pdf"
    multipage_added = SYNTHETIC_DIR / "multipage_added_candidate.pdf"
    multipage_reordered = SYNTHETIC_DIR / "multipage_reordered_candidate.pdf"

    build_multipage_record(multipage_reference)
    shutil.copyfile(multipage_reference, multipage_clean)
    build_multipage_record(multipage_tampered, result_value="REVIEW")
    build_multipage_record(multipage_missing, page_sequence=("overview", "completion"))
    build_multipage_record(
        multipage_added,
        page_sequence=("overview", "results", "completion", "added"),
    )
    build_multipage_record(
        multipage_reordered,
        page_sequence=("overview", "completion", "results"),
    )

    rendered_phase2: list[Path] = []
    rendered_phase2.extend(render_pdf_pages(multipage_reference, "multipage_reference"))
    rendered_phase2.extend(render_pdf_pages(multipage_clean, "multipage_clean_candidate"))
    rendered_phase2.extend(render_pdf_pages(multipage_tampered, "multipage_tampered_candidate"))

    reference_values = {
        "name": "ARIA NOVA",
        "identifier": "LGX-2042-017",
        "date": "17 JUNE 2042",
        "result": "DISTINCTION",
    }
    legitimate_values = {
        "name": "LYRA QUILL",
        "identifier": "LGX-2042-042",
        "date": "21 JUNE 2042",
        "result": "MERIT",
    }
    template_reference = SYNTHETIC_DIR / "template_reference.pdf"
    template_legitimate = SYNTHETIC_DIR / "template_legitimate_candidate.pdf"
    template_manipulated = SYNTHETIC_DIR / "template_manipulated_candidate.pdf"
    build_template_record(template_reference, values=reference_values)
    build_template_record(template_legitimate, values=legitimate_values)
    build_template_record(template_manipulated, values=legitimate_values, manipulated=True)
    rendered_phase2.extend(render_pdf_pages(template_reference, "template_reference"))
    rendered_phase2.extend(render_pdf_pages(template_legitimate, "template_legitimate_candidate"))
    rendered_phase2.extend(render_pdf_pages(template_manipulated, "template_manipulated_candidate"))

    rendered_width, rendered_height = cv2.imread(str(rendered_phase2[0]), cv2.IMREAD_COLOR).shape[1::-1]
    multipage_bbox = _normalized_rect(MULTIPAGE_RESULT_RECT)
    template_bbox = _normalized_rect(TEMPLATE_MANIPULATED_RECT)
    multipage_mask = EXPECTED_DIR / "multipage_tamper_mask_page_2.png"
    template_mask = EXPECTED_DIR / "template_manipulated_mask.png"
    _write_mask(multipage_mask, multipage_bbox, rendered_width, rendered_height)
    _write_mask(template_mask, template_bbox, rendered_width, rendered_height)

    raster_png = SYNTHETIC_DIR / "raster_only_document.png"
    raster_pdf = SYNTHETIC_DIR / "raster_only_document.pdf"
    raster_tokens = build_raster_only_fixture(raster_png, raster_pdf)

    generated_paths = [
        multipage_reference,
        multipage_clean,
        multipage_tampered,
        multipage_missing,
        multipage_added,
        multipage_reordered,
        template_reference,
        template_legitimate,
        template_manipulated,
        raster_png,
        raster_pdf,
        multipage_mask,
        template_mask,
        *rendered_phase2,
    ]
    files = {
        path.relative_to(ROOT).as_posix(): {"sha256": sha256(path), "bytes": path.stat().st_size}
        for path in sorted(generated_paths)
    }
    return {
        "schema_version": "2.0",
        "synthetic": True,
        "notice": "Every person, institution, identifier, result, logo, and seal is fictional.",
        "render": {
            "longest_dimension": RENDER_LONGEST,
            "width": rendered_width,
            "height": rendered_height,
        },
        "phase1_regression": {
            "manifest": "expected/manifest.json",
            "minimum_localization_iou": 0.30,
        },
        "multi_page": {
            "reference": "synthetic/multipage_reference.pdf",
            "clean_candidate": "synthetic/multipage_clean_candidate.pdf",
            "tampered_candidate": "synthetic/multipage_tampered_candidate.pdf",
            "page_count": 3,
            "page_sequence": ["record overview", "module results", "completion summary"],
            "tampering": {
                "page_number": 2,
                "field": "VERIFICATION",
                "before": "DISTINCTION",
                "after": "REVIEW",
                "normalized_bbox": multipage_bbox,
                "mask": "expected/multipage_tamper_mask_page_2.png",
                "expected_categories": ["text_content_change"],
                "minimum_localization_iou": 0.30,
            },
            "thresholds": {"clean_max_risk": 20, "tampered_min_risk": 50},
        },
        "page_anomalies": {
            "missing": {
                "candidate": "synthetic/multipage_missing_candidate.pdf",
                "candidate_sequence": ["record overview", "completion summary"],
                "expected_category": "page_missing",
            },
            "added": {
                "candidate": "synthetic/multipage_added_candidate.pdf",
                "candidate_sequence": [
                    "record overview",
                    "module results",
                    "completion summary",
                    "added review note",
                ],
                "expected_category": "page_added",
            },
            "reordered": {
                "candidate": "synthetic/multipage_reordered_candidate.pdf",
                "candidate_sequence": ["record overview", "completion summary", "module results"],
                "expected_category": "page_reordered",
            },
        },
        "template": {
            "reference": "synthetic/template_reference.pdf",
            "legitimate_candidate": "synthetic/template_legitimate_candidate.pdf",
            "manipulated_candidate": "synthetic/template_manipulated_candidate.pdf",
            "variable_fields": [
                {
                    "label": key,
                    "role": "variable",
                    "reference": reference_values[key],
                    "legitimate": legitimate_values[key],
                }
                for key in ("name", "identifier", "date", "result")
            ],
            "legitimate": {
                "expected_behavior": "informational_variable_value_changes",
                "maximum_tampering_risk": 35,
            },
            "manipulated": {
                "field": "RESULT",
                "normalized_bbox": template_bbox,
                "mask": "expected/template_manipulated_mask.png",
                "expected_categories": ["typography_inconsistency", "background_compositing"],
                "minimum_tampering_risk": 50,
                "minimum_localization_iou": 0.30,
            },
        },
        "raster_ocr": {
            "pdf": "synthetic/raster_only_document.pdf",
            "png": "synthetic/raster_only_document.png",
            "no_embedded_text": True,
            "expected_tokens": raster_tokens,
            "minimum_token_matches": 6,
            "approximate_box_minimum_iou": 0.08,
        },
        "files": files,
    }


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
    phase2_manifest = _generate_phase2_fixtures()
    (EXPECTED_DIR / "phase2_manifest.json").write_text(
        json.dumps(phase2_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
