#!/usr/bin/env python3
"""Extract green-highlighted product rows from Excel into products.json."""

from __future__ import annotations

import json
import os
from pathlib import Path

import openpyxl

GREEN_FILL = "FFB6D7A8"
EXCEL_NAME = "STOK GRUP KODLAR - SİTE.xlsx"
IMAGE_DIR = Path("netsis stok görselleri")
OUTPUT = Path("products.json")


def cell_fill_rgb(cell) -> str | None:
    if not cell.fill or not cell.fill.patternType:
        return None
    return str(cell.fill.fgColor.rgb)


def find_local_image(stock_code: str, image_dir: Path) -> str | None:
    code = str(stock_code).strip()
    candidates = [code, code.lstrip("0")]
    if len(code) >= 7 and code.startswith("03"):
        candidates.append("01" + code[2:])
    if len(code) >= 7 and code.startswith("01"):
        candidates.append("03" + code[2:])
    seen: set[str] = set()
    for cand in candidates:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        for ext in (".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG"):
            candidate = image_dir / f"{cand}{ext}"
            if candidate.is_file():
                return str(candidate.resolve())
    return None


def extract_products(
    excel_path: Path = Path(EXCEL_NAME),
    image_dir: Path = IMAGE_DIR,
    output_path: Path = OUTPUT,
) -> list[dict]:
    wb = openpyxl.load_workbook(excel_path)
    ws = wb.worksheets[0]

    products: list[dict] = []
    for row in ws.iter_rows(min_row=2):
        stock_cell = row[0]
        if stock_cell.value is None:
            continue
        if cell_fill_rgb(stock_cell) != GREEN_FILL:
            continue

        stock_code = str(stock_cell.value).strip()
        title = str(row[1].value or "").strip()
        main_category = str(row[2].value or "").strip()
        sub_category = str(row[3].value or "").strip()
        sub_sub_category = str(row[4].value or "").strip()
        brand = str(row[6].value or "").strip()

        local_image = find_local_image(stock_code, image_dir)

        products.append(
            {
                "stock_code": stock_code,
                "title": title,
                "main_category": main_category,
                "sub_category": sub_category,
                "sub_sub_category": sub_sub_category,
                "brand": brand,
                "local_image": local_image,
            }
        )

    output_path.write_text(
        json.dumps(products, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return products


def main() -> None:
    products = extract_products()
    with_local = sum(1 for p in products if p["local_image"])
    print(f"Extracted {len(products)} green products -> {OUTPUT}")
    print(f"Local images found: {with_local}/{len(products)}")


if __name__ == "__main__":
    main()
