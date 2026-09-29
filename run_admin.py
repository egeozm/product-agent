#!/usr/bin/env python3
"""Automate Aterstore admin product entry from products.json."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse, parse_qs, urlunparse

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout, sync_playwright


SCRIPT_DIR = Path(__file__).resolve().parent
IMAGE_DIR = SCRIPT_DIR / "netsis stok görselleri"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG"}
BULK_UPLOAD_BATCH_SIZE = 25
_images_bulk_uploaded = False


def stock_code_candidates(stock_code: str) -> list[str]:
    code = str(stock_code).strip()
    candidates = [code, code.lstrip("0")]
    if len(code) >= 7 and code.startswith("03"):
        candidates.append("01" + code[2:])
    if len(code) >= 7 and code.startswith("01"):
        candidates.append("03" + code[2:])
    seen: set[str] = set()
    result: list[str] = []
    for cand in candidates:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        result.append(cand)
    return result


def resolve_local_image(stock_code: str) -> str | None:
    for cand in stock_code_candidates(stock_code):
        for ext in IMAGE_EXTENSIONS:
            path = IMAGE_DIR / f"{cand}{ext}"
            if path.is_file():
                return str(path.resolve())
    return None


def collect_all_local_images() -> list[str]:
    if not IMAGE_DIR.is_dir():
        return []
    return sorted(
        str(path.resolve())
        for path in IMAGE_DIR.iterdir()
        if path.is_file() and path.suffix in IMAGE_EXTENSIONS
    )


CONFIG_PATH = SCRIPT_DIR / "config.json"
PRODUCTS_PATH = SCRIPT_DIR / "products.json"
REPORT_PATH = SCRIPT_DIR / "report.csv"
LOG_DIR = SCRIPT_DIR / "logs"


@dataclass
class ProductResult:
    stock_code: str
    title: str
    status: str
    action: str = ""
    image_source: str = ""
    missing_image: bool = False
    missing_categories: list[str] = field(default_factory=list)
    missing_brand: bool = False
    notes: str = ""


def load_config() -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "admin_url": "https://adresgezginitasarim.com/aterstore/admin/",
        "product_form_url": "https://adresgezginitasarim.com/aterstore/admin/?modul=icerik&sayfa=icerik&m_id=12",
        "username": "",
        "password": "",
        "headless": False,
        "slow_mo_ms": 800,
        "delay_between_steps_ms": 1500,
        "delay_between_products_sec": 0,
        "wait_for_network_idle": True,
        "dry_run": False,
        "limit": None,
        "manual_login": False,
        "bulk_upload_images": False,
    }
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    return cfg


def log_step(msg: str, cfg: dict[str, Any]) -> None:
    print(f"  >> {msg}", flush=True)
    delay = cfg.get("delay_between_steps_ms", 1500) / 1000
    if delay > 0:
        time.sleep(delay)


def wait_after_action(page: Page, cfg: dict[str, Any], fallback_ms: int = 1000) -> None:
    if cfg.get("wait_for_network_idle", True):
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except PlaywrightTimeout:
            page.wait_for_timeout(fallback_ms)
    else:
        page.wait_for_timeout(fallback_ms)


def ensure_logged_in(page: Page, cfg: dict[str, Any], manual: bool = False) -> None:
    log_step("Opening admin login page", cfg)
    page.goto(cfg["admin_url"], wait_until="commit", timeout=120000)
    page.wait_for_timeout(2000)

    needs_login = page.locator("#kullanici_adi").count() > 0
    if not needs_login:
        if page.locator("#sidebar, .sidebar, .nav-main, .side-content").count() > 0:
            return
        raise RuntimeError("Could not detect admin login state.")

    if manual:
        print("Please log in manually in the browser window. Waiting up to 5 minutes...")
        page.wait_for_function(
            "() => !document.querySelector('#kullanici_adi')",
            timeout=300000,
        )
        page.wait_for_load_state("networkidle", timeout=90000)
        return

    username = cfg.get("username", "")
    password = cfg.get("password", "")
    if not username or username == "YOUR_USERNAME":
        raise RuntimeError(
            "Set username/password in config.json or run with --manual-login."
        )
    log_step("Submitting login credentials", cfg)
    page.fill("#kullanici_adi", username)
    page.fill("#sifre", password)
    page.click('button[type="submit"]:has-text("Giriş")')
    wait_after_action(page, cfg, fallback_ms=2000)
    if page.locator("#kullanici_adi").count() > 0:
        raise RuntimeError("Login failed. Check credentials in config.json.")


def load_products(limit: int | None = None) -> list[dict[str, Any]]:
    products = json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))
    if limit:
        products = products[:limit]
    return products


def normalize_text(value: str) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.upper()
    text = text.replace("İ", "I").replace("I", "I")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def texts_match(a: str, b: str) -> bool:
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def append_query(url: str, params: dict[str, str]) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key, value in params.items():
        query[key] = [value]
    new_query = urlencode({k: v[0] for k, v in query.items()})
    return urlunparse(parsed._replace(query=new_query))


def login(page: Page, cfg: dict[str, Any], manual: bool = False) -> None:
    ensure_logged_in(page, cfg, manual=manual)


def discover_form_fields(page: Page) -> dict[str, str]:
    return page.evaluate(
        """() => {
          const result = {};
          const labels = [...document.querySelectorAll('label, .control-label, legend, h4, h5, .form-group > div:first-child')];
          const inputs = [...document.querySelectorAll('input, textarea, select')];
          for (const label of labels) {
            const text = (label.innerText || label.textContent || '').trim();
            if (!text) continue;
            let input = null;
            const forId = label.getAttribute('for');
            if (forId) input = document.getElementById(forId);
            if (!input) input = label.querySelector('input, textarea, select');
            if (!input) {
              const group = label.closest('.form-group');
              if (group) input = group.querySelector('input:not([type=hidden]), textarea, select');
            }
            if (input && (input.name || input.id)) {
              result[text] = input.name || input.id;
            }
          }
          return result;
        }"""
    )


def fill_input_by_hint(page: Page, hints: list[str], value: str) -> bool:
    for hint in hints:
        norm_hint = normalize_text(hint)
        filled = page.evaluate(
            """({ hint, value }) => {
              const norm = (s) => (s || '')
                .normalize('NFKD')
                .replace(/[\u0300-\u036f]/g, '')
                .replace(/İ/g, 'I').replace(/ı/g, 'i')
                .toUpperCase()
                .replace(/\\s+/g, ' ')
                .trim();
              const target = norm(hint);
              const inputs = [...document.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=file]), textarea, select')];
              for (const input of inputs) {
                const name = norm(input.name || input.id || '');
                if (name.includes(target) || target.includes(name)) {
                  input.value = value;
                  input.dispatchEvent(new Event('input', { bubbles: true }));
                  input.dispatchEvent(new Event('change', { bubbles: true }));
                  return true;
                }
              }
              const labels = [...document.querySelectorAll('label, .control-label, legend, h4, h5')];
              for (const label of labels) {
                const text = norm(label.innerText || label.textContent || '');
                if (!text || (!text.includes(target) && !target.includes(text))) continue;
                let input = null;
                const forId = label.getAttribute('for');
                if (forId) input = document.getElementById(forId);
                if (!input) input = label.querySelector('input:not([type=hidden]):not([type=file]), textarea, select');
                if (!input) {
                  const group = label.closest('.form-group');
                  if (group) input = group.querySelector('input:not([type=hidden]):not([type=file]), textarea, select');
                }
                if (input) {
                  input.value = value;
                  input.dispatchEvent(new Event('input', { bubbles: true }));
                  input.dispatchEvent(new Event('change', { bubbles: true }));
                  return true;
                }
              }
              return false;
            }""",
            {"hint": hint, "value": value},
        )
        if filled:
            return True
        if norm_hint in ("BASLIK", "BAŞLIK") or "BASLIK" in norm_hint:
            for sel in ('input[name*="baslik" i]', 'input[id*="baslik" i]', 'textarea[name*="baslik" i]'):
                loc = page.locator(sel)
                if loc.count():
                    loc.first.fill(value)
                    return True
        if "STOK" in norm_hint:
            for sel in ('input[name*="stok" i]', 'input[id*="stok" i]'):
                loc = page.locator(sel)
                if loc.count():
                    loc.first.fill(value)
                    return True
    return False


def open_add_form(page: Page, cfg: dict[str, Any]) -> None:
    log_step("Opening new product form", cfg)
    page.goto(cfg["product_form_url"], wait_until="commit", timeout=120000)
    wait_after_action(page, cfg, fallback_ms=1500)


def open_edit_form(page: Page, cfg: dict[str, Any], content_id: str) -> None:
    log_step(f"Opening edit form for content id {content_id}", cfg)
    url = append_query(cfg["product_form_url"], {"x_id": content_id})
    page.goto(url, wait_until="commit", timeout=120000)
    wait_after_action(page, cfg, fallback_ms=1500)


def find_existing_content_id(
    page: Page, cfg: dict[str, Any], stock_code: str
) -> str | None:
    log_step(f"Checking duplicate for stock code {stock_code}", cfg)
    list_url = append_query(cfg["product_form_url"], {"ara": stock_code})
    page.goto(list_url, wait_until="commit", timeout=120000)
    wait_after_action(page, cfg, fallback_ms=2000)

    row = page.locator(f"tr:has-text('{stock_code}')").first
    if row.count() == 0:
        return None

    link = row.locator("a[href*='x_id=']").first
    if link.count() == 0:
        edit = row.locator("[onclick*='x_id']").first
        if edit.count():
            onclick = edit.get_attribute("onclick") or ""
            match = re.search(r"x_id[=\'\"](\d+)", onclick)
            if match:
                return match.group(1)
        return None

    href = link.get_attribute("href") or ""
    match = re.search(r"x_id=(\d+)", href)
    return match.group(1) if match else None


def close_image_modal(page: Page, cfg: dict[str, Any], *, confirm: bool = False) -> None:
    modal = page.locator("#ortam_kutuphanesi")
    if modal.count() == 0 or not modal.is_visible():
        return
    log_step("Closing file library modal", cfg)
    if confirm and cover_image_is_set(page):
        tamam = page.locator(
            '#ortam_kutuphanesi .modal-footer button[data-dismiss="modal"]:has-text("Tamam")'
        ).first
        if tamam.count() and tamam.is_visible():
            tamam.click()
            page.locator("#ortam_kutuphanesi").wait_for(state="hidden", timeout=10000)
            wait_after_action(page, cfg, fallback_ms=500)
            return
    close_btn = page.locator(
        '#ortam_kutuphanesi .si-close, '
        '#ortam_kutuphanesi button:has-text("Kapat")'
    ).first
    if close_btn.count() and close_btn.is_visible():
        close_btn.click()
    else:
        page.keyboard.press("Escape")
    page.locator("#ortam_kutuphanesi").wait_for(state="hidden", timeout=10000)
    wait_after_action(page, cfg, fallback_ms=500)


def open_cover_image_picker(page: Page, cfg: dict[str, Any]) -> None:
    log_step("Opening cover image picker", cfg)
    modal = page.locator("#ortam_kutuphanesi")
    if modal.count() and modal.is_visible():
        close_image_modal(page, cfg)
    btn = page.locator("button[onclick*=\"resim_sec('kapak-resim'\"]").first
    if btn.count() == 0:
        kapak = page.locator("text=Kapak Resim").first
        if kapak.count():
            section = kapak.locator("xpath=ancestor::*[self::div or self::fieldset][1]")
            btn = section.locator("button:has-text('Resim Seç')").first
    if btn.count() == 0:
        btn = page.locator('button:has-text("Resim Seç")').first
    btn.click()
    page.locator("#ortam_kutuphanesi.in, #ortam_kutuphanesi.show").wait_for(state="visible", timeout=15000)
    wait_after_action(page, cfg, fallback_ms=1500)


def library_thumbnail_locator(page: Page):
    """Only the clickable thumbnail tiles — not s_dosya_id_*, s_dosya_url_*, etc."""
    return page.locator("#tum_dosyalar .img-link[id^='s_dosya_']")


def wait_for_library_results(page: Page, cfg: dict[str, Any], timeout_ms: int = 20000) -> bool:
    try:
        page.wait_for_selector(
            "#tum_dosyalar .img-link[id^='s_dosya_']",
            state="visible",
            timeout=timeout_ms,
        )
    except PlaywrightTimeout:
        return False
    wait_after_action(page, cfg, fallback_ms=800)
    return True


def library_result_count(page: Page) -> int:
    return library_thumbnail_locator(page).count()


def click_grid_top_left(page: Page, cfg: dict[str, Any]) -> bool:
    grid = page.locator("#tum_dosyalar")
    if grid.count() == 0:
        return False
    box = grid.bounding_box()
    if not box:
        log_step("Could not get library grid position", cfg)
        return False
    count = library_result_count(page)
    log_step(f"Clicking top-left grid position ({count} visible)", cfg)
    page.mouse.click(box["x"] + 60, box["y"] + 60)
    wait_after_action(page, cfg, fallback_ms=500)
    return cover_image_is_set(page)


def search_and_select_in_library(
    page: Page, cfg: dict[str, Any], stock_code: str
) -> bool:
    page.locator('a[href="#dosyalar"]').click()
    wait_after_action(page, cfg, fallback_ms=800)

    search_input = page.locator("#s_dosya_ara")
    if search_input.count() == 0:
        log_step("Library search box not found", cfg)
        return False

    found = False
    for candidate in stock_code_candidates(stock_code):
        log_step(f"Searching library for {candidate}", cfg)
        search_input.fill("")
        search_input.fill(candidate)
        page.evaluate("() => { if (typeof dosya_ara === 'function') dosya_ara(); }")
        if wait_for_library_results(page, cfg, timeout_ms=10000):
            found = True
            break

    if not found:
        log_step(f"No library results for {stock_code}", cfg)
        return False

    if not click_grid_top_left(page, cfg):
        log_step("Clicked grid but cover image was not set", cfg)
        return False

    log_step(f"Cover image set for {stock_code}", cfg)
    close_image_modal(page, cfg, confirm=True)
    return cover_image_is_set(page)


def cover_image_is_set(page: Page) -> bool:
    cover_id = page.locator("#v_dosya_kapak-resim_1").input_value()
    if cover_id:
        return True
    preview = page.locator("#k_resim_kapak-resim_1 img, #k_resim_kapak-resim_1 .img-link")
    return preview.count() > 0 and preview.first.is_visible()


def wait_for_dropzone_idle(page: Page, timeout_ms: int = 90000) -> bool:
    try:
        page.wait_for_function(
            """() => {
              const form = document.querySelector('#dosya');
              if (!form) return false;
              if (typeof Dropzone !== 'undefined' && form.dropzone) {
                const dz = form.dropzone;
                if (dz.getUploadingFiles().length > 0) return false;
                if (dz.getQueuedFiles().length > 0) return false;
                return dz.files.some(f => f.status === 'success') || dz.files.length === 0;
              }
              return true;
            }""",
            timeout=timeout_ms,
        )
        return True
    except PlaywrightTimeout:
        return False


def bulk_upload_all_local_images(page: Page, cfg: dict[str, Any]) -> int:
    paths = collect_all_local_images()
    if not paths:
        log_step("No local images found to bulk upload", cfg)
        return 0

    log_step(f"Bulk uploading {len(paths)} images from {IMAGE_DIR}", cfg)
    open_cover_image_picker(page, cfg)
    page.locator('a[href="#yukle"]').click()
    wait_after_action(page, cfg, fallback_ms=1000)

    file_input = page.locator("#dosya input[type='file']").first
    if file_input.count() == 0:
        file_input = page.locator("input[type='file']").first
    if file_input.count() == 0:
        log_step("Upload file input not found", cfg)
        close_image_modal(page, cfg)
        return 0

    total_batches = (len(paths) + BULK_UPLOAD_BATCH_SIZE - 1) // BULK_UPLOAD_BATCH_SIZE
    uploaded = 0

    for batch_idx, start in enumerate(range(0, len(paths), BULK_UPLOAD_BATCH_SIZE), start=1):
        batch = paths[start : start + BULK_UPLOAD_BATCH_SIZE]
        log_step(
            f"Bulk upload batch {batch_idx}/{total_batches} "
            f"({start + len(batch)}/{len(paths)} files)",
            cfg,
        )
        file_input.set_input_files(batch)
        if not wait_for_dropzone_idle(page):
            log_step(f"Upload timed out on batch {batch_idx}", cfg)
        wait_after_action(page, cfg, fallback_ms=2000)
        uploaded += len(batch)

    close_image_modal(page, cfg, confirm=False)
    log_step(f"Bulk upload complete: {uploaded} files", cfg)
    return uploaded


def set_cover_image(
    page: Page, cfg: dict[str, Any], stock_code: str
) -> tuple[bool, str]:
    open_cover_image_picker(page, cfg)
    if search_and_select_in_library(page, cfg, stock_code):
        return True, "site_library"
    close_image_modal(page, cfg)
    log_step(f"No image in library for {stock_code}", cfg)
    return False, "missing"


def expand_panel(page: Page, block_id: str, cfg: dict[str, Any]) -> None:
    header = page.locator(f"#{block_id}")
    if header.count() == 0:
        return
    block = header.locator("xpath=ancestor::div[contains(@class,'block')][1]")
    if block.locator(".block-opt-hidden").count() > 0 or not block.locator(".block-content").first.is_visible():
        page.evaluate(f"(id) => {{ if (typeof block_toggle === 'function') block_toggle(id); }}", block_id)
        wait_after_action(page, cfg, fallback_ms=800)


def check_named_checkbox(
    page: Page, label_text: str, scope: str, cfg: dict[str, Any]
) -> bool:
    labels = page.locator(scope)
    count = labels.count()
    for i in range(count):
        label = labels.nth(i)
        text = (label.inner_text() or "").strip()
        if not texts_match(text, label_text):
            continue
        label.scroll_into_view_if_needed()
        checkbox = label.locator("input[type='checkbox']").first
        if checkbox.count() and checkbox.is_checked():
            return True
        label.click()
        wait_after_action(page, cfg, fallback_ms=400)
        if checkbox.count() and checkbox.is_checked():
            return True
        checked = label.evaluate(
            """(el) => {
              const cb = el.querySelector('input[type="checkbox"]');
              if (cb && !cb.checked) {
                cb.checked = true;
                cb.dispatchEvent(new Event('change', { bubbles: true }));
              }
              return cb ? cb.checked : false;
            }"""
        )
        return bool(checked)
    return False


def set_categories(page: Page, cfg: dict[str, Any], product: dict[str, Any]) -> list[str]:
    log_step("Selecting categories", cfg)
    expand_panel(page, "block_kat", cfg)
    missing: list[str] = []
    for key in ("main_category", "sub_category", "sub_sub_category"):
        value = product.get(key, "")
        if not value:
            continue
        if not check_named_checkbox(page, value, "#kategori_tazele label", cfg):
            missing.append(value)
    return missing


def set_brand(page: Page, cfg: dict[str, Any], brand: str) -> bool:
    if not brand:
        return True
    log_step(f"Selecting brand: {brand}", cfg)
    expand_panel(page, "block_iliski_19", cfg)
    search = page.locator("#markalar-ara")
    if search.count():
        search.fill(brand.split()[0])
        wait_after_action(page, cfg, fallback_ms=800)
    return check_named_checkbox(page, brand, "#markalar-listesi label", cfg)


def save_product(page: Page, cfg: dict[str, Any], save_and_new: bool = True) -> None:
    label = "Kaydet ve Yeni Ekle" if save_and_new else "Kaydet"
    log_step(f"Clicking {label}", cfg)
    if save_and_new:
        btn = page.locator('input[name="y_islem"][value="Kaydet ve Yeni Ekle"], button:has-text("Kaydet ve Yeni Ekle")').first
    else:
        btn = page.locator('input[name="y_islem"][value="Kaydet"], button:has-text("Kaydet")').first
    btn.click()
    wait_after_action(page, cfg, fallback_ms=2000)


def process_product(
    page: Page,
    cfg: dict[str, Any],
    product: dict[str, Any],
    field_cache: dict[str, str],
    dry_run: bool,
) -> ProductResult:
    global _images_bulk_uploaded

    stock_code = product["stock_code"]
    title = product["title"]
    result = ProductResult(stock_code=stock_code, title=title, status="pending")

    existing_id = find_existing_content_id(page, cfg, stock_code)
    if existing_id:
        open_edit_form(page, cfg, existing_id)
        result.action = "update"
    else:
        open_add_form(page, cfg)
        result.action = "create"

    if dry_run:
        result.status = "dry_run"
        return result

    if not dry_run and cfg.get("bulk_upload_images") and not _images_bulk_uploaded:
        bulk_upload_all_local_images(page, cfg)
        _images_bulk_uploaded = True

    if not field_cache:
        discovered = discover_form_fields(page)
        for k, v in discovered.items():
            field_cache[k] = v

    log_step("Filling title and stock code", cfg)
    if page.locator("#i_adi_1").count():
        page.locator("#i_adi_1").fill(title)
    else:
        fill_input_by_hint(page, ["BAŞLIK", "Baslik", "Başlık", "baslik", "i_adi"], title)
    if page.locator("#stok-kodu_1").count():
        page.locator("#stok-kodu_1").fill(stock_code)
    else:
        fill_input_by_hint(page, ["Stok Kodu", "STOK KODU", "stok"], stock_code)

    image_ok, image_source = set_cover_image(page, cfg, stock_code)
    result.image_source = image_source
    result.missing_image = not image_ok

    result.missing_categories = set_categories(page, cfg, product)
    result.missing_brand = not set_brand(page, cfg, product.get("brand", ""))

    save_product(page, cfg, save_and_new=True)
    result.status = "ok" if not result.missing_categories and not result.missing_brand else "partial"
    if result.missing_image:
        result.notes = "No image found in library."
    if result.missing_categories:
        result.notes += f" Missing categories: {', '.join(result.missing_categories)}."
    if result.missing_brand:
        result.notes += " Missing brand."
    return result


REPORT_FIELDS = [
    "stock_code",
    "title",
    "status",
    "action",
    "image_source",
    "missing_image",
    "missing_categories",
    "missing_brand",
    "notes",
]


def result_to_row(result: ProductResult) -> dict[str, Any]:
    return {
        "stock_code": result.stock_code,
        "title": result.title,
        "status": result.status,
        "action": result.action,
        "image_source": result.image_source,
        "missing_image": result.missing_image,
        "missing_categories": "|".join(result.missing_categories),
        "missing_brand": result.missing_brand,
        "notes": result.notes.strip(),
    }


def load_report() -> list[ProductResult]:
    if not REPORT_PATH.exists() or REPORT_PATH.stat().st_size == 0:
        return []
    results: list[ProductResult] = []
    with REPORT_PATH.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cats = [c for c in (row.get("missing_categories") or "").split("|") if c]
            results.append(
                ProductResult(
                    stock_code=row.get("stock_code", ""),
                    title=row.get("title", ""),
                    status=row.get("status", ""),
                    action=row.get("action", ""),
                    image_source=row.get("image_source", ""),
                    missing_image=str(row.get("missing_image", "")).lower() == "true",
                    missing_categories=cats,
                    missing_brand=str(row.get("missing_brand", "")).lower() == "true",
                    notes=row.get("notes", ""),
                )
            )
    return results


def init_report(*, resume: bool = False) -> None:
    if resume and REPORT_PATH.exists() and REPORT_PATH.stat().st_size > 0:
        return
    write_report([])


def write_report(results: list[ProductResult]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = REPORT_PATH.with_suffix(".csv.tmp")
    with tmp_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        for result in results:
            writer.writerow(result_to_row(result))
        f.flush()
        os.fsync(f.fileno())
    tmp_path.replace(REPORT_PATH)


def append_report_row(result: ProductResult, results: list[ProductResult]) -> None:
    results.append(result)
    write_report(results)


def print_summary(results: list[ProductResult]) -> None:
    total = len(results)
    ok = sum(1 for r in results if r.status == "ok")
    partial = sum(1 for r in results if r.status == "partial")
    failed = sum(1 for r in results if r.status not in {"ok", "partial", "dry_run"})
    with_image = sum(1 for r in results if not r.missing_image)
    print("\n=== Run Summary ===")
    print(f"Total processed: {total}")
    print(f"Success: {ok}")
    print(f"Partial (missing category/brand): {partial}")
    print(f"Failed/other: {failed}")
    print(f"With image: {with_image}/{total}")
    print(f"Report written to: {REPORT_PATH.resolve()}")


def run_simulate_report(products: list[dict[str, Any]], *, resume: bool = False) -> list[ProductResult]:
    results: list[ProductResult] = []
    if not resume:
        write_report([])

    for idx, product in enumerate(products, start=1):
        stock_code = product["stock_code"]
        title = product["title"]
        local_image = product.get("local_image") or resolve_local_image(stock_code)
        result = ProductResult(
            stock_code=stock_code,
            title=title,
            status="simulated",
            action="simulate",
            image_source="site_library" if local_image else "missing",
            missing_image=not bool(local_image),
            notes="Simulated row — no browser run.",
        )
        append_report_row(result, results)
        print(
            f"[{idx}/{len(products)}] {stock_code} -> simulated "
            f"(image={result.image_source}) | report.csv: {len(results)} row(s)",
            flush=True,
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Aterstore admin product automation")
    parser.add_argument("--dry-run", action="store_true", help="Navigate/search only, do not save")
    parser.add_argument("--limit", type=int, default=None, help="Process only first N products")
    parser.add_argument("--manual-login", action="store_true", help="Log in manually in the browser")
    parser.add_argument("--step", action="store_true", help="Pause before each product (press Enter to continue)")
    parser.add_argument("--start", type=int, default=0, help="Start index in products.json")
    parser.add_argument(
        "--bulk-upload",
        action="store_true",
        help="Upload all local images once before the first product (skip if already in library)",
    )
    parser.add_argument(
        "--simulate-report",
        action="store_true",
        help="Write one simulated report row per product without opening the browser",
    )
    args = parser.parse_args()

    cfg = load_config()
    if args.bulk_upload:
        cfg["bulk_upload_images"] = True
    dry_run = args.dry_run or bool(cfg.get("dry_run"))
    manual_login = args.manual_login or bool(cfg.get("manual_login"))
    step_mode = args.step
    limit = args.limit or cfg.get("limit")
    all_products = load_products()
    total_all = len(all_products)
    products = all_products
    if args.start:
        products = products[args.start :]
    if limit:
        products = products[:limit]

    if not products:
        print("No products found in products.json")
        sys.exit(1)

    LOG_DIR.mkdir(exist_ok=True)

    if args.simulate_report:
        results = run_simulate_report(products, resume=bool(args.start))
        print_summary(results)
        return

    if args.start:
        results = load_report()
        print(f"Resuming from product index {args.start} — {len(results)} row(s) in report.csv")
    else:
        init_report(resume=False)
        results = []
    field_cache: dict[str, str] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=bool(cfg.get("headless", False)),
            slow_mo=int(cfg.get("slow_mo_ms", 100)),
        )
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = context.new_page()
        login(page, cfg, manual=manual_login)
        log_step("Login complete — starting product loop", cfg)

        product_delay = cfg.get("delay_between_products_sec", 0)

        for idx, product in enumerate(products, start=1):
            global_idx = args.start + idx
            print(
                f"\n[{global_idx}/{total_all}] {product['stock_code']} - {product['title'][:60]}"
            )
            if step_mode:
                input("  Press Enter to process this product (Ctrl+C to stop)... ")
            try:
                result = process_product(page, cfg, product, field_cache, dry_run=dry_run)
            except Exception as exc:  # noqa: BLE001
                result = ProductResult(
                    stock_code=product["stock_code"],
                    title=product["title"],
                    status="error",
                    notes=str(exc),
                )
                screenshot = LOG_DIR / f"error_{product['stock_code']}.png"
                try:
                    page.screenshot(path=str(screenshot), full_page=True)
                except Exception:
                    pass
                print(f"  ERROR: {exc}")
            else:
                print(f"  -> {result.status} ({result.action}) image={result.image_source or 'none'}")
            append_report_row(result, results)
            print(
                f"  -> report.csv updated ({len(results)} row(s)) at {REPORT_PATH}",
                flush=True,
            )
            if idx < len(products) and product_delay > 0:
                log_step(f"Waiting {product_delay}s before next product", cfg)
                time.sleep(product_delay)

        browser.close()

    print_summary(results)


if __name__ == "__main__":
    main()
