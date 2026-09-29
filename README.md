# Aterstore Product Automation

Automates entering green-highlighted products from Excel into the Aterstore admin panel.

## Setup

```bash
pip3 install -r requirements.txt --break-system-packages
python3 -m playwright install chromium
cp config.example.json config.json
# Edit config.json with admin username/password
python3 extract_products.py
python3 run_admin.py
```

## Scripts

| Script | Purpose |
|--------|---------|
| `extract_products.py` | Reads green rows from Excel -> `products.json` |
| `discover_admin.py` | Logs in and dumps form HTML/selectors to `discovery/` |
| `run_admin.py` | Main automation: login, create/update products, images, categories, brands |

## Options

```bash
python3 run_admin.py --dry-run      # Navigate only, no saves
python3 run_admin.py --limit 5      # Process first 5 products
python3 run_admin.py --start 10     # Skip first 10 products
```

## Image resolution (per product)

1. Search site file library (`Dosya Kütüphanesi`) by stock code
2. If not found, upload from `netsis stok görselleri/<code>.jpg`
3. If still missing, save product without image and flag in `report.csv`

## Output

- `products.json` — 138 extracted products
- `report.csv` — per-product status after run
- `logs/error_<code>.png` — screenshots on failure

## Admin login fields (discovered)

- Username: `#kullanici_adi`
- Password: `#sifre`
- Submit: `button[type="submit"]` ("Giriş")

## File library JS (discovered)

- Open picker: `resim_sec(deger, kod)`
- Search input: `#s_dosya_ara` + `dosya_ara()`
- Select file: `dosya_sec(n)` on thumbnails in `#tum_dosyalar`
