#!/usr/bin/env python3
"""Inspect admin login and product form DOM for selector discovery."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def load_config() -> dict:
    path = Path("config.json")
    if not path.exists():
        print("Missing config.json - copy from config.example.json and add credentials.")
        sys.exit(1)
    return json.loads(path.read_text(encoding="utf-8"))


def discover() -> None:
    cfg = load_config()
    out = Path("discovery")
    out.mkdir(exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=bool(cfg.get("headless", True)))
        page = browser.new_page()
        print(">> Loading login page...")
        page.goto(cfg["admin_url"], wait_until="commit", timeout=120000)
        page.wait_for_timeout(2000)
        (out / "login.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(out / "login.png"), full_page=True)

        inputs = page.evaluate(
            """() => [...document.querySelectorAll('input, button, textarea, select')].map(el => ({
                tag: el.tagName,
                type: el.type || '',
                name: el.name || '',
                id: el.id || '',
                placeholder: el.placeholder || '',
                text: (el.innerText || el.value || '').slice(0, 80)
            }))"""
        )
        (out / "login_inputs.json").write_text(
            json.dumps(inputs, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        username = cfg["username"]
        password = cfg["password"]
        if username in ("", "YOUR_USERNAME"):
            print("Set real credentials in config.json before discovery.")
            browser.close()
            return

        print(">> Logging in...")
        page.fill("#kullanici_adi", username)
        page.fill("#sifre", password)
        page.click('button[type="submit"]:has-text("Giriş")')
        page.wait_for_load_state("networkidle", timeout=120000)
        page.wait_for_timeout(2000)
        (out / "after_login.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(out / "after_login.png"), full_page=True)

        print(">> Loading product form...")
        page.goto(cfg["product_form_url"], wait_until="commit", timeout=120000)
        page.wait_for_timeout(3000)
        (out / "product_form.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(out / "product_form.png"), full_page=True)

        form_info = page.evaluate(
            """() => {
              const labels = [...document.querySelectorAll('label, .control-label, .form-label, h4, h5, legend')].map(el => ({
                text: (el.innerText || '').trim().slice(0, 120),
                for: el.getAttribute('for') || ''
              }));
              const inputs = [...document.querySelectorAll('input, textarea, select, button')].map(el => ({
                tag: el.tagName,
                type: el.type || '',
                name: el.name || '',
                id: el.id || '',
                className: el.className || '',
                placeholder: el.placeholder || '',
                text: (el.innerText || el.value || '').slice(0, 80)
              }));
              const checkboxes = [...document.querySelectorAll('input[type="checkbox"]')].map(el => ({
                name: el.name || '',
                id: el.id || '',
                value: el.value || '',
                label: el.closest('label')?.innerText?.trim() || el.parentElement?.innerText?.trim() || ''
              }));
              return { labels, inputs, checkboxes };
            }"""
        )
        (out / "product_form_fields.json").write_text(
            json.dumps(form_info, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Discovery saved to {out.resolve()}")
        browser.close()


if __name__ == "__main__":
    discover()
