#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shop Voucher Monitor & Scanner Module
Scans and monitors active shop discount vouchers across e-commerce storefront categories,
supporting direct HTML parsing from Shopee storefronts (e.g. Yuedpao Official).
"""
import os
import sys
import time
import json
import ssl
import math
import re
import argparse
import tempfile
import urllib.request
import urllib.parse

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(BASE_DIR, ".shop_vouchers_cache.json")
CACHE_SCHEMA_VERSION = 3

def load_env():
    env_path = os.environ.get("BOT_ENV_FILE", os.path.join(BASE_DIR, ".env"))
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except Exception:
            pass

load_env()

def env_float(name, default, minimum=0.0, maximum=None):
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value) or value < minimum or (maximum is not None and value > maximum):
        return default
    return value

CACHE_TTL = env_float("SHOP_VOUCHER_CACHE_TTL", 120.0, minimum=5.0, maximum=86400.0)
SHOP_VOUCHER_REQUEST_TIMEOUT = env_float(
    "SHOP_VOUCHER_REQUEST_TIMEOUT", 15.0, minimum=1.0, maximum=60.0
)
SHOPEE_API_BASE = "https://shopee.co.th/api/v4"
SHOPEE_CURRENCY_SCALE = 100000
SHOPEE_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
BUILTIN_SHOPEE_SHOP_URLS = (
    (
        "https://shopee.co.th/i..77"
        "?categoryId=100629&entryPoint=ShopByPDP&itemId=48503136286"
    ),
    (
        "https://shopee.co.th/korawutphulueanglue"
        "?categoryId=100637&entryPoint=ShopByPDP&itemId=52460011928"
    ),
    (
        "https://shopee.co.th/adidas"
        "?categoryId=100637&entryPoint=ShopByPDP&itemId=29956821236"
    ),
)
DEFAULT_SHOPEE_SHOP_URLS = tuple(
    target.strip()
    for target in os.environ.get(
        "SHOPEE_SHOP_URLS", ",".join(BUILTIN_SHOPEE_SHOP_URLS)
    ).split(",")
    if target.strip()
)
SSL_CONTEXT = None
if os.environ.get("BYPASS_SSL_VERIFY", "false").lower() in ("true", "1", "yes"):
    SSL_CONTEXT = ssl._create_unverified_context()

# ─────────────────────────────────────────────
# HTML Voucher Parser for Shopee Storefronts
# ─────────────────────────────────────────────
def parse_shopee_html_vouchers(html_content, shop_name="Yuedpao Official", shop_username="yuedpao_official", category="Fashion"):
    """
    Parse Shopee storefront vouchers directly from HTML elements (.shop-page__vouchers, .image-carousel__item).
    Extracts discount title, min spend, max discount, claimed %, expiry, and product scope.
    """
    vouchers = []
    seen = set()
    
    if HAS_BS4:
        soup = BeautifulSoup(html_content, "html.parser")
        items = soup.select(".image-carousel__item, .shop-page__vouchers li, .MBklXU")
        if not items:
            items = soup.find_all(class_=re.compile(r"MBklXU|image-carousel__item"))
            
        for item in items:
            text = item.get_text(separator=" ", strip=True)
            if not text or "ส่วนลด" not in text:
                continue
            if text in seen:
                continue
            seen.add(text)
            
            # Extract title
            title_match = re.search(r"ส่วนลด\s*(?:\d+%\s*|\฿\s*[\d,]+)", text)
            title = title_match.group(0).strip() if title_match else "ส่วนลดพิเศษ"
            
            # Min spend & Max discount
            min_spend = 0
            max_discount = 0
            min_match = re.search(r"ขั้นต่ำ\s*฿\s*([\d,]+)", text)
            if min_match:
                min_spend = int(min_match.group(1).replace(",", ""))
                
            max_match = re.search(r"ลดสูงสุด\s*฿\s*([\d,]+)", text)
            if max_match:
                max_discount = int(max_match.group(1).replace(",", ""))
                
            # Discount value & type
            pct_match = re.search(r"ส่วนลด\s*(\d+)%", title)
            fixed_match = re.search(r"ส่วนลด\s*฿\s*([\d,]+)", title)
            
            discount_type = "percentage"
            discount_value = 0
            if pct_match:
                discount_type = "percentage"
                discount_value = int(pct_match.group(1))
                if max_discount == 0:
                    max_discount = discount_value
            elif fixed_match:
                discount_type = "fixed"
                discount_value = int(fixed_match.group(1).replace(",", ""))
                if max_discount == 0:
                    max_discount = discount_value
                    
            # Scope
            scope = "สินค้าที่กำหนด" if "สินค้าที่กำหนด" in text else "ใช้ได้ทั้งร้าน"
            
            # Claimed %
            claimed_pct = 0
            claimed_match = re.search(r"ใช้แล้ว\s*(\d+)%", text)
            if claimed_match:
                claimed_pct = int(claimed_match.group(1))
                
            # Expiry
            expiry_days = 3
            expiry_info = ""
            if "เหลือ" in text:
                exp_match = re.search(r"เหลือ\s*(\d+)\s*(ชั่วโมง|วัน)", text)
                if exp_match:
                    val = int(exp_match.group(1))
                    unit = exp_match.group(2)
                    expiry_days = max(1, val if unit == "วัน" else round(val / 24, 1))
                    expiry_info = f"เหลือ {val} {unit}"
            elif "ใช้ได้ถึง" in text:
                exp_match = re.search(r"ใช้ได้ถึง:\s*([\d\.]+)", text)
                if exp_match:
                    expiry_info = f"ใช้ได้ถึง: {exp_match.group(1)}"
                    
            v_num = len(vouchers) + 1
            code_prefix = shop_username.upper().replace("_OFFICIAL", "").replace("_", "")[:7]
            code = f"{code_prefix}{discount_value}{'PCT' if discount_type == 'percentage' else 'OFF'}"
            if any(v['code'] == code for v in vouchers):
                code = f"{code}_{v_num}"
                
            vouchers.append({
                "voucher_id": f"VOUCHER_{v_num:02d}_{discount_type.upper()}_{discount_value}",
                "code": code,
                "title": f"🎟️ {title} ({scope})",
                "discount_type": discount_type,
                "discount_value": discount_value,
                "max_discount": max_discount,
                "min_spend": min_spend,
                "claimed_pct": claimed_pct,
                "expiry_days": expiry_days,
                "expiry_info": expiry_info,
                "terms": f"{title} | ขั้นต่ำ ฿{min_spend:,} | ลดสูงสุด ฿{max_discount:,} | {scope}"
            })
    else:
        # Regex Fallback if bs4 is missing
        raw_items = re.findall(r'<li[^>]*class="[^"]*image-carousel__item[^"]*"[^>]*>(.*?)</li>', html_content, re.DOTALL)
        if not raw_items:
            raw_items = re.findall(r'<div[^>]*class="[^"]*MBklXU[^"]*"[^>]*>(.*?)</div>\s*</div>\s*</div>', html_content, re.DOTALL)
            
        for item_html in raw_items:
            clean_text = re.sub(r'<[^>]+>', ' ', item_html)
            clean_text = ' '.join(clean_text.split())
            if "ส่วนลด" not in clean_text or clean_text in seen:
                continue
            seen.add(clean_text)
            
            title_m = re.search(r"ส่วนลด\s*(?:\d+%\s*|\฿\s*[\d,]+)", clean_text)
            title = title_m.group(0).strip() if title_m else "ส่วนลดพิเศษ"
            
            min_spend = 0
            max_discount = 0
            min_m = re.search(r"ขั้นต่ำ\s*฿\s*([\d,]+)", clean_text)
            if min_m:
                min_spend = int(min_m.group(1).replace(",", ""))
            max_m = re.search(r"ลดสูงสุด\s*฿\s*([\d,]+)", clean_text)
            if max_m:
                max_discount = int(max_m.group(1).replace(",", ""))
                
            pct_m = re.search(r"ส่วนลด\s*(\d+)%", title)
            fixed_m = re.search(r"ส่วนลด\s*฿\s*([\d,]+)", title)
            discount_type = "percentage"
            discount_value = 0
            if pct_m:
                discount_type = "percentage"
                discount_value = int(pct_m.group(1))
                if max_discount == 0:
                    max_discount = discount_value
            elif fixed_m:
                discount_type = "fixed"
                discount_value = int(fixed_m.group(1).replace(",", ""))
                if max_discount == 0:
                    max_discount = discount_value
                    
            scope = "สินค้าที่กำหนด" if "สินค้าที่กำหนด" in clean_text else "ใช้ได้ทั้งร้าน"
            claimed_pct = 0
            claimed_m = re.search(r"ใช้แล้ว\s*(\d+)%", clean_text)
            if claimed_m:
                claimed_pct = int(claimed_m.group(1))
                
            expiry_days = 3
            expiry_info = ""
            if "เหลือ" in clean_text:
                exp_m = re.search(r"เหลือ\s*(\d+)\s*(ชั่วโมง|วัน)", clean_text)
                if exp_m:
                    val = int(exp_m.group(1))
                    unit = exp_m.group(2)
                    expiry_days = max(1, val if unit == "วัน" else round(val / 24, 1))
                    expiry_info = f"เหลือ {val} {unit}"
            elif "ใช้ได้ถึง" in clean_text:
                exp_m = re.search(r"ใช้ได้ถึง:\s*([\d\.]+)", clean_text)
                if exp_m:
                    expiry_info = f"ใช้ได้ถึง: {exp_m.group(1)}"
                    
            v_num = len(vouchers) + 1
            code_prefix = shop_username.upper().replace("_OFFICIAL", "").replace("_", "")[:7]
            code = f"{code_prefix}{discount_value}{'PCT' if discount_type == 'percentage' else 'OFF'}"
            if any(v['code'] == code for v in vouchers):
                code = f"{code}_{v_num}"
                
            vouchers.append({
                "voucher_id": f"VOUCHER_{v_num:02d}_{discount_type.upper()}_{discount_value}",
                "code": code,
                "title": f"🎟️ {title} ({scope})",
                "discount_type": discount_type,
                "discount_value": discount_value,
                "max_discount": max_discount,
                "min_spend": min_spend,
                "claimed_pct": claimed_pct,
                "expiry_days": expiry_days,
                "expiry_info": expiry_info,
                "terms": f"{title} | ขั้นต่ำ ฿{min_spend:,} | ลดสูงสุด ฿{max_discount:,} | {scope}"
            })

    return {
        "shop_id": "5001",
        "shop_name": shop_name,
        "shop_username": shop_username,
        "category": category,
        "badge": "Mall",
        "rating": 4.9,
        "avatar": "https://img.icons8.com/color/96/t-shirt.png",
        "vouchers": vouchers
    }

# ─────────────────────────────────────────────
# Legacy sample data kept only for compatibility with older imports.
# Live scans never use this list.
# ─────────────────────────────────────────────
MONITORED_SHOPS = [
    {
        "shop_id": "5001",
        "shop_name": "Yuedpao Official Store (เสื้อยืดพับได้ ยืดเปล่า)",
        "shop_username": "yuedpao_official",
        "category": "Fashion",
        "badge": "Mall",
        "rating": 4.9,
        "avatar": "https://img.icons8.com/color/96/t-shirt.png",
        "vouchers": [
            {
                "voucher_id": "YUEDPAO_01_PCT_5",
                "code": "YUEDPAO5PCT",
                "title": "🎟️ ส่วนลด 5% (ใช้ได้ทั้งร้าน)",
                "discount_type": "percentage",
                "discount_value": 5,
                "max_discount": 20,
                "min_spend": 590,
                "claimed_pct": 99,
                "expiry_days": 1,
                "terms": "ส่วนลด 5% | ขั้นต่ำ ฿590 | ลดสูงสุด ฿20 | ใช้ได้ทั้งร้าน"
            },
            {
                "voucher_id": "YUEDPAO_02_PCT_25",
                "code": "YUEDPAO25PCT",
                "title": "🎟️ ส่วนลด 25% (ใช้ได้ทั้งร้าน)",
                "discount_type": "percentage",
                "discount_value": 25,
                "max_discount": 30,
                "min_spend": 725,
                "claimed_pct": 0,
                "expiry_days": 5,
                "terms": "ส่วนลด 25% | ขั้นต่ำ ฿725 | ลดสูงสุด ฿30 | ใช้ได้ทั้งร้าน"
            },
            {
                "voucher_id": "YUEDPAO_03_PCT_15",
                "code": "YUEDPAO15PCT",
                "title": "🎟️ ส่วนลด 15% (ใช้ได้ทั้งร้าน)",
                "discount_type": "percentage",
                "discount_value": 15,
                "max_discount": 40,
                "min_spend": 790,
                "claimed_pct": 51,
                "expiry_days": 5,
                "terms": "ส่วนลด 15% | ขั้นต่ำ ฿790 | ลดสูงสุด ฿40 | ใช้ได้ทั้งร้าน"
            },
            {
                "voucher_id": "YUEDPAO_04_FIXED_60",
                "code": "YUEDPAO60OFF",
                "title": "🎟️ ส่วนลด ฿60 (สินค้าที่กำหนด)",
                "discount_type": "fixed",
                "discount_value": 60,
                "max_discount": 60,
                "min_spend": 100,
                "claimed_pct": 0,
                "expiry_days": 7,
                "terms": "ส่วนลด ฿60 | ขั้นต่ำ ฿100 | สินค้าที่กำหนด"
            },
            {
                "voucher_id": "YUEDPAO_05_FIXED_30",
                "code": "YUEDPAO30OFF",
                "title": "🎟️ ส่วนลด ฿30 (สินค้าที่กำหนด)",
                "discount_type": "fixed",
                "discount_value": 30,
                "max_discount": 30,
                "min_spend": 100,
                "claimed_pct": 0,
                "expiry_days": 7,
                "terms": "ส่วนลด ฿30 | ขั้นต่ำ ฿100 | สินค้าที่กำหนด"
            },
            {
                "voucher_id": "YUEDPAO_06_FIXED_100",
                "code": "YUEDPAO100OFF",
                "title": "🎟️ ส่วนลด ฿100 (สินค้าที่กำหนด)",
                "discount_type": "fixed",
                "discount_value": 100,
                "max_discount": 100,
                "min_spend": 200,
                "claimed_pct": 0,
                "expiry_days": 5,
                "terms": "ส่วนลด ฿100 | ขั้นต่ำ ฿200 | สินค้าที่กำหนด"
            },
            {
                "voucher_id": "YUEDPAO_07_FIXED_100_ALT",
                "code": "YUEDPAO100OFF_ALT",
                "title": "🎟️ ส่วนลด ฿100 (สินค้าที่กำหนด - รอบใหม่)",
                "discount_type": "fixed",
                "discount_value": 100,
                "max_discount": 100,
                "min_spend": 200,
                "claimed_pct": 0,
                "expiry_days": 7,
                "terms": "ส่วนลด ฿100 | ขั้นต่ำ ฿200 | สินค้าที่กำหนด"
            },
            {
                "voucher_id": "YUEDPAO_08_FIXED_5",
                "code": "YUEDPAO5OFF",
                "title": "🎟️ ส่วนลด ฿5 (สินค้าที่กำหนด)",
                "discount_type": "fixed",
                "discount_value": 5,
                "max_discount": 5,
                "min_spend": 499,
                "claimed_pct": 0,
                "expiry_days": 5,
                "terms": "ส่วนลด ฿5 | ขั้นต่ำ ฿499 | สินค้าที่กำหนด"
            },
            {
                "voucher_id": "YUEDPAO_09_FIXED_10",
                "code": "YUEDPAO10OFF",
                "title": "🎟️ ส่วนลด ฿10 (สินค้าที่กำหนด)",
                "discount_type": "fixed",
                "discount_value": 10,
                "max_discount": 10,
                "min_spend": 750,
                "claimed_pct": 0,
                "expiry_days": 5,
                "terms": "ส่วนลด ฿10 | ขั้นต่ำ ฿750 | สินค้าที่กำหนด"
            },
            {
                "voucher_id": "YUEDPAO_10_FIXED_25",
                "code": "YUEDPAO25OFF",
                "title": "🎟️ ส่วนลด ฿25 (สินค้าที่กำหนด)",
                "discount_type": "fixed",
                "discount_value": 25,
                "max_discount": 25,
                "min_spend": 1500,
                "claimed_pct": 0,
                "expiry_days": 5,
                "terms": "ส่วนลด ฿25 | ขั้นต่ำ ฿1,500 | สินค้าที่กำหนด"
            },
            {
                "voucher_id": "YUEDPAO_11_FIXED_45",
                "code": "YUEDPAO45OFF",
                "title": "🎟️ ส่วนลด ฿45 (สินค้าที่กำหนด)",
                "discount_type": "fixed",
                "discount_value": 45,
                "max_discount": 45,
                "min_spend": 2000,
                "claimed_pct": 0,
                "expiry_days": 5,
                "terms": "ส่วนลด ฿45 | ขั้นต่ำ ฿2,000 | สินค้าที่กำหนด"
            }
        ]
    },
    {
        "shop_id": "1001",
        "shop_name": "Shopee Official Flagship Store",
        "shop_username": "shopee_official",
        "category": "Supermarket",
        "badge": "Mall",
        "rating": 4.9,
        "avatar": "https://img.icons8.com/color/96/shopee.png",
        "vouchers": [
            {
                "voucher_id": "SHOPEE50OFF",
                "code": "SP50OFFMALL",
                "title": "ลดเพิ่ม 50% ไม่มีขั้นต่ำ",
                "discount_type": "percentage",
                "discount_value": 50,
                "max_discount": 150,
                "min_spend": 0,
                "claimed_pct": 78,
                "expiry_days": 2,
                "terms": "สำหรับสินค้าหมวดหมู่ Shopee Supermarket เท่านั้น"
            },
            {
                "voucher_id": "SPCOINBACK15",
                "code": "SP15COINS",
                "title": "รับเงินคืน 15% Coins",
                "discount_type": "coins",
                "discount_value": 15,
                "max_discount": 300,
                "min_spend": 500,
                "claimed_pct": 45,
                "expiry_days": 5,
                "terms": "คืน Coin สูงสุด 300 coins ใช้ได้ทั้งร้าน"
            }
        ]
    },
    {
        "shop_id": "3001",
        "shop_name": "AVC & Free Shipping Vouchers Center (ศูนย์โค้ดส่งฟรี & โค้ดคุ้ม)",
        "shop_username": "avc_fsv_official",
        "category": "Free Shipping & AVC",
        "badge": "AVC / FSV",
        "rating": 5.0,
        "avatar": "https://img.icons8.com/color/96/free-shipping.png",
        "vouchers": [
            {
                "voucher_id": "FSV0MIN",
                "code": "FSVMIN0BAHT",
                "title": "🚚 โค้ดส่งฟรี ขั้นต่ำ ฿0 (AVC-FSV)",
                "discount_type": "shipping",
                "discount_value": 40,
                "max_discount": 40,
                "min_spend": 0,
                "claimed_pct": 91,
                "expiry_days": 1,
                "terms": "โค้ดส่งฟรีพิเศษไม่มีขั้นต่ำ ใช้ได้กับทุกร้านค้าประเภท AVC/FSV"
            },
            {
                "voucher_id": "FSV99PAY",
                "code": "FSV99SPAY",
                "title": "🚚 ส่งฟรี ขั้นต่ำ ฿99 (ShopeePay / SPayLater)",
                "discount_type": "shipping",
                "discount_value": 40,
                "max_discount": 40,
                "min_spend": 99,
                "claimed_pct": 75,
                "expiry_days": 3,
                "terms": "ส่งฟรีเมื่อชำระเงินผ่าน ShopeePay หรือ SPayLater"
            },
            {
                "voucher_id": "AVC30PCT",
                "code": "AVCCODENUM30",
                "title": "🏷️ โค้ดส่วนลด 30% ร้านโค้ดคุ้ม (AVC)",
                "discount_type": "percentage",
                "discount_value": 30,
                "max_discount": 200,
                "min_spend": 0,
                "claimed_pct": 89,
                "expiry_days": 2,
                "terms": "ส่วนลด 30% สูงสุด 200 บาท สำหรับร้านค้าที่เข้าร่วมโปรแกรมโค้ดคุ้ม"
            },
            {
                "voucher_id": "AVCCOIN25",
                "code": "AVCCOIN25PCT",
                "title": "🪙 โค้ดรับเงินคืน 25% Coins ร้านโค้ดคุ้ม",
                "discount_type": "coins",
                "discount_value": 25,
                "max_discount": 500,
                "min_spend": 200,
                "claimed_pct": 82,
                "expiry_days": 4,
                "terms": "รับเงินคืน 25% Coins สูงสุด 500 Coins เมื่อซื้อครบ 200 บาท"
            },
            {
                "voucher_id": "FSVMIDNIGHT",
                "code": "FSVMIDNIGHT0",
                "title": "🌙 โค้ดส่งฟรี รอบเที่ยงคืน ขั้นต่ำ ฿0",
                "discount_type": "shipping",
                "discount_value": 50,
                "max_discount": 50,
                "min_spend": 0,
                "claimed_pct": 96,
                "expiry_days": 1,
                "terms": "โค้ดส่งฟรีรอบพิเศษ 00:00 - 02:00 น. สนับสนุนค่าจัดส่งสูงสุด 50 บาท"
            }
        ]
    },
    {
        "shop_id": "4001",
        "shop_name": "Shopee SLV Seller Vouchers Hub (ศูนย์รวมโค้ดส่วนลดผู้ขาย slv-sellervoucher)",
        "shop_username": "slv_sellervoucher_official",
        "category": "SLV Seller Vouchers",
        "badge": "SLV Seller",
        "rating": 5.0,
        "avatar": "https://img.icons8.com/color/96/voucher.png",
        "vouchers": [
            {
                "voucher_id": "SLV150BAHT",
                "code": "SLV150SELLER",
                "title": "🏷️ โค้ดส่วนลดร้านค้า SLV 150 บาท",
                "discount_type": "fixed",
                "discount_value": 150,
                "max_discount": 150,
                "min_spend": 599,
                "claimed_pct": 87,
                "expiry_days": 2,
                "terms": "โค้ดส่วนลดหน้าร้านค้าแคมเปญ SLV Seller Voucher ใช้ได้กับร้านค้าเข้าร่วม"
            },
            {
                "voucher_id": "SLV20PCT",
                "code": "SLV20OFFSELLER",
                "title": "🏷️ โค้ดส่วนลดร้านค้า SLV 20% ไม่มีขั้นต่ำ",
                "discount_type": "percentage",
                "discount_value": 20,
                "max_discount": 100,
                "min_spend": 0,
                "claimed_pct": 93,
                "expiry_days": 1,
                "terms": "ลดทันที 20% ไม่มีขั้นต่ำ สูงสุด 100 บาท สำหรับสินค้าผู้ขายแคมเปญ SLV"
            },
            {
                "voucher_id": "SLVCOIN15",
                "code": "SLVCOIN15CB",
                "title": "🪙 โค้ดรับเงินคืน 15% Coins จากผู้ขาย SLV",
                "discount_type": "coins",
                "discount_value": 15,
                "max_discount": 250,
                "min_spend": 300,
                "claimed_pct": 79,
                "expiry_days": 4,
                "terms": "รับเงินคืน 15% Coins สูงสุด 250 Coins เมื่อซื้อครบ 300 บาท"
            },
            {
                "voucher_id": "SLV500OFF",
                "code": "SLV500VIP",
                "title": "🔥 โค้ดส่วนลดพิเศษผู้ขาย SLV 500 บาท",
                "discount_type": "fixed",
                "discount_value": 500,
                "max_discount": 500,
                "min_spend": 2500,
                "claimed_pct": 95,
                "expiry_days": 3,
                "terms": "ส่วนลด 500 บาท เมื่อซื้อครบ 2,500 บาท สำหรับร้านค้ารายย่อยและแบรนด์ SLV"
            }
        ]
    },
    {
        "shop_id": "1002",
        "shop_name": "Xiaomi Official Store",
        "shop_username": "xiaomi_thailand",
        "category": "Electronics",
        "badge": "Mall",
        "rating": 4.9,
        "avatar": "https://img.icons8.com/color/96/xiaomi.png",
        "vouchers": [
            {
                "voucher_id": "XIAOMI1000",
                "code": "XIAOMI1KCOUPON",
                "title": "ส่วนลด 1,000 บาท",
                "discount_type": "fixed",
                "discount_value": 1000,
                "max_discount": 1000,
                "min_spend": 7990,
                "claimed_pct": 92,
                "expiry_days": 1,
                "terms": "ใช้ซื้อสมาร์ทโฟนและ Smart Home รุ่นที่ร่วมรายการ"
            },
            {
                "voucher_id": "XIAOMIFREEGIFT",
                "code": "XMACC12OFF",
                "title": "ลด 12% สินค้า Gadget",
                "discount_type": "percentage",
                "discount_value": 12,
                "max_discount": 400,
                "min_spend": 999,
                "claimed_pct": 60,
                "expiry_days": 4,
                "terms": "สำหรับอุปกรณ์เสริม สายชาร์จ และหูฟัง"
            }
        ]
    },
    {
        "shop_id": "1003",
        "shop_name": "Samsung Official Store",
        "shop_username": "samsung_thailand",
        "category": "Electronics",
        "badge": "Mall",
        "rating": 4.8,
        "avatar": "https://img.icons8.com/color/96/samsung.png",
        "vouchers": [
            {
                "voucher_id": "SAMSUNGGALAXY",
                "code": "SSGAL2000",
                "title": "ส่วนลดพิเศษ 2,000 บาท",
                "discount_type": "fixed",
                "discount_value": 2000,
                "max_discount": 2000,
                "min_spend": 15900,
                "claimed_pct": 84,
                "expiry_days": 3,
                "terms": "เฉพาะ Galaxy S Series และ Z Fold/Flip"
            }
        ]
    },
    {
        "shop_id": "1004",
        "shop_name": "Uniqlo & Fashion Trends",
        "shop_username": "fashion_trends_th",
        "category": "Fashion",
        "badge": "Star+",
        "rating": 4.7,
        "avatar": "https://img.icons8.com/color/96/t-shirt.png",
        "vouchers": [
            {
                "voucher_id": "FASHION200",
                "code": "FS200BAHT",
                "title": "ลด 200 บาท เมื่องานครบ 1,200",
                "discount_type": "fixed",
                "discount_value": 200,
                "max_discount": 200,
                "min_spend": 1200,
                "claimed_pct": 52,
                "expiry_days": 6,
                "terms": "สำหรับเสื้อผ้าแฟชั่นชาย-หญิง แบรนด์ยอดฮิต"
            }
        ]
    },
    {
        "shop_id": "1005",
        "shop_name": "Beauty Secret Thailand",
        "shop_username": "beauty_secret_official",
        "category": "Beauty",
        "badge": "Star+",
        "rating": 4.9,
        "avatar": "https://img.icons8.com/color/96/lipstick.png",
        "vouchers": [
            {
                "voucher_id": "BEAUTY30PCT",
                "code": "GLOW30OFF",
                "title": "ลดทันที 30% สกินแคร์",
                "discount_type": "percentage",
                "discount_value": 30,
                "max_discount": 350,
                "min_spend": 499,
                "claimed_pct": 89,
                "expiry_days": 1,
                "terms": "ใช้ซื้อเซรั่มและครีมกันแดดทุกแบรนด์ในร้าน"
            }
        ]
    },
    {
        "shop_id": "1006",
        "shop_name": "Home & Living Smart Shop",
        "shop_username": "home_decor_th",
        "category": "Home & Living",
        "badge": "Star+",
        "rating": 4.8,
        "avatar": "https://img.icons8.com/color/96/home.png",
        "vouchers": [
            {
                "voucher_id": "HOME150",
                "code": "DECOR150",
                "title": "ส่วนลด 150 บาท แต่งบ้านสุดคุ้ม",
                "discount_type": "fixed",
                "discount_value": 150,
                "max_discount": 150,
                "min_spend": 899,
                "claimed_pct": 40,
                "expiry_days": 7,
                "terms": "เฟอร์นิเจอร์ และโคมไฟตกแต่ง"
            }
        ]
    },
    {
        "shop_id": "1007",
        "shop_name": "AGY Trading Bot & Digital EAs",
        "shop_username": "agy_trading_official",
        "category": "Digital Services",
        "badge": "Verified",
        "rating": 5.0,
        "avatar": "https://img.icons8.com/color/96/bot.png",
        "vouchers": [
            {
                "voucher_id": "GOLDVIP500",
                "code": "AGYGOLD500",
                "title": "ลด 500 บาท สำหรับสคริปต์ EA ทองคำ",
                "discount_type": "fixed",
                "discount_value": 500,
                "max_discount": 500,
                "min_spend": 1990,
                "claimed_pct": 95,
                "expiry_days": 2,
                "terms": "สคริปต์ Gold Micro & High Profit EA สิทธิพิเศษ"
            }
        ]
    },
    {
        "shop_id": "2001",
        "shop_name": "ร้านพริกแกงใต้ป้าต้อย (Southern Chili Paste)",
        "shop_username": "paatoy_curry_local",
        "category": "Food & Grocery",
        "badge": "SME / Local",
        "rating": 4.9,
        "avatar": "https://img.icons8.com/color/96/chili-pepper.png",
        "vouchers": [
            {
                "voucher_id": "PAATOY20",
                "code": "PAATOY20OFF",
                "title": "ลด 20 บาท ไม่มีขั้นต่ำ (พริกแกงโฮมเมด)",
                "discount_type": "fixed",
                "discount_value": 20,
                "max_discount": 20,
                "min_spend": 0,
                "claimed_pct": 82,
                "expiry_days": 3,
                "terms": "พริกแกงเผ็ด แกงส้ม ทำสดใหม่รายวันจากชุมชน"
            },
            {
                "voucher_id": "SOUTHERN50",
                "code": "SOUTH50BAHT",
                "title": "ลด 50 บาท เมื่อซื้อครบ 250",
                "discount_type": "fixed",
                "discount_value": 50,
                "max_discount": 50,
                "min_spend": 250,
                "claimed_pct": 65,
                "expiry_days": 5,
                "terms": "ใช้ซื้อสินค้าครบ 250 บาทขึ้นไปในร้านป้าต้อย"
            }
        ]
    },
    {
        "shop_id": "2002",
        "shop_name": "Craft & Leather Studio (ช่างเชษฐ์เครื่องหนัง)",
        "shop_username": "chett_craft_leather",
        "category": "Fashion & Craft",
        "badge": "SME / Local",
        "rating": 4.9,
        "avatar": "https://img.icons8.com/color/96/belt.png",
        "vouchers": [
            {
                "voucher_id": "LEATHER100",
                "code": "CHETT100OFF",
                "title": "ลด 100 บาท งานคราฟต์กระเป๋าหนังแท้",
                "discount_type": "fixed",
                "discount_value": 100,
                "max_discount": 100,
                "min_spend": 650,
                "claimed_pct": 58,
                "expiry_days": 4,
                "terms": "กระเป๋าสตางค์ เข็มขัดหนังแท้ตัดเย็บมือทุกชิ้น"
            }
        ]
    },
    {
        "shop_id": "2003",
        "shop_name": "ร้านน้าสมชายฮาร์ดแวร์ & อุปกรณ์ช่าง",
        "shop_username": "somchai_hardware_local",
        "category": "Home & Tools",
        "badge": "SME / Local",
        "rating": 4.8,
        "avatar": "https://img.icons8.com/color/96/hammer.png",
        "vouchers": [
            {
                "voucher_id": "SOMCHAI30",
                "code": "SOMCHAI30OFF",
                "title": "ลด 30 บาท เครื่องมือช่าง & น็อตสกรู",
                "discount_type": "fixed",
                "discount_value": 30,
                "max_discount": 30,
                "min_spend": 199,
                "claimed_pct": 73,
                "expiry_days": 2,
                "terms": "สำหรับน็อต ไขควง และอุปกรณ์ซ่อมบ้านทั่วไป"
            }
        ]
    },
    {
        "shop_id": "2004",
        "shop_name": "ร้านเมล็ดพันธุ์ผักสวนครัวบ้านป้าแจ่ม",
        "shop_username": "pajam_organic_seeds",
        "category": "Gardening",
        "badge": "SME / Local",
        "rating": 4.9,
        "avatar": "https://img.icons8.com/color/96/potted-plant.png",
        "vouchers": [
            {
                "voucher_id": "GREEN15PCT",
                "code": "PAJAM15SEEDS",
                "title": "ลด 15% เมล็ดพันธุ์ผักอินทรีย์พื้นบ้าน",
                "discount_type": "percentage",
                "discount_value": 15,
                "max_discount": 60,
                "min_spend": 120,
                "claimed_pct": 88,
                "expiry_days": 6,
                "terms": "เมล็ดพันธุ์กะเพรา โหระพา พริกขี้หนูสวน อัตราการงอกสูง"
            }
        ]
    },
    {
        "shop_id": "2005",
        "shop_name": "ร้านทาสแมวอุปกรณ์สัตว์เลี้ยงรายย่อย",
        "shop_username": "tasmeow_local_pet",
        "category": "Pet Supplies",
        "badge": "SME / Local",
        "rating": 4.8,
        "avatar": "https://img.icons8.com/color/96/cat.png",
        "vouchers": [
            {
                "voucher_id": "MEOW40OFF",
                "code": "TASMEOW40",
                "title": "ลด 40 บาท อาหารแมว & ที่ลับเล็บ",
                "discount_type": "fixed",
                "discount_value": 40,
                "max_discount": 40,
                "min_spend": 299,
                "claimed_pct": 64,
                "expiry_days": 3,
                "terms": "ของเล่นทำมือ และขนมแมวเลียออร์แกนิก"
            }
        ]
    }
]

def load_vouchers_cache(max_age_seconds=CACHE_TTL):
    """Load cached shop vouchers if not expired."""
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        cached_at = data.get("_cached_at", 0)
        if (
            time.time() - cached_at < max_age_seconds
            and data.get("_schema_version") == CACHE_SCHEMA_VERSION
            and data.get("source") == "shopee_api"
            and "shops" in data
        ):
            return data
    except Exception:
        pass
    return None

def save_vouchers_cache(data):
    """Save shop vouchers cache atomically."""
    try:
        data.setdefault("source", "shopee_api")
        data["_cached_at"] = time.time()
        data["_schema_version"] = CACHE_SCHEMA_VERSION
        dir_name = os.path.dirname(CACHE_FILE) or "."
        with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8") as tf:
            json.dump(data, tf, ensure_ascii=False, indent=2)
            temp_name = tf.name
        os.replace(temp_name, CACHE_FILE)
    except Exception as e:
        print(f"Warning: failed to save vouchers cache: {e}", file=sys.stderr)

class ShopeeVoucherFetchError(RuntimeError):
    """Raised when Shopee live voucher data cannot be fetched or validated."""


def _resolve_shopee_shop_target(url_or_username):
    raw_target = str(url_or_username or "").strip()
    if not raw_target:
        raise ShopeeVoucherFetchError("ไม่ได้ระบุ URL หรือ username ของร้าน Shopee")

    if not raw_target.lower().startswith(("http://", "https://")):
        username = raw_target.strip("@").strip()
    else:
        try:
            parsed = urllib.parse.urlparse(raw_target)
            port = parsed.port
        except ValueError as exc:
            raise ShopeeVoucherFetchError("URL ร้าน Shopee ไม่ถูกต้อง") from exc
        host = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme != "https"
            or host not in {"shopee.co.th", "www.shopee.co.th"}
            or port not in {None, 443}
        ):
            raise ShopeeVoucherFetchError(
                "รองรับเฉพาะ HTTPS URL บนโดเมน shopee.co.th"
            )
        username = urllib.parse.unquote(parsed.path).strip("/").split("/")[0]

    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", username or ""):
        raise ShopeeVoucherFetchError("username ร้าน Shopee ไม่ถูกต้อง")
    return username, f"https://shopee.co.th/{username}"


def _extract_shopee_category_id(url_or_username):
    raw_target = str(url_or_username or "").strip()
    if not raw_target.lower().startswith(("http://", "https://")):
        return ""
    try:
        parsed = urllib.parse.urlparse(raw_target)
        query = urllib.parse.parse_qs(parsed.query)
    except ValueError:
        return ""
    category_id = str((query.get("categoryId") or [""])[0]).strip()
    return category_id if re.fullmatch(r"\d{1,20}", category_id) else ""


def _fetch_shopee_json(url, referer):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/138.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Accept-Language": "th-TH,th;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": referer,
    }
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(
            request,
            context=SSL_CONTEXT,
            timeout=SHOP_VOUCHER_REQUEST_TIMEOUT,
        ) as response:
            raw = response.read(SHOPEE_MAX_RESPONSE_BYTES + 1)
    except Exception as exc:
        raise ShopeeVoucherFetchError(f"Shopee API ติดต่อไม่ได้: {exc}") from exc

    if len(raw) > SHOPEE_MAX_RESPONSE_BYTES:
        raise ShopeeVoucherFetchError("Shopee API ส่งข้อมูลเกินขนาดที่อนุญาต")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ShopeeVoucherFetchError("Shopee API ส่งข้อมูลที่ไม่ใช่ JSON") from exc
    if not isinstance(payload, dict):
        raise ShopeeVoucherFetchError("รูปแบบข้อมูลจาก Shopee API ไม่ถูกต้อง")
    if payload.get("error") not in (None, 0):
        message = payload.get("error_msg") or payload.get("message") or payload["error"]
        raise ShopeeVoucherFetchError(f"Shopee API ปฏิเสธคำขอ: {message}")
    return payload


def _number(value, default=0):
    if value is None or isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _shopee_money(value):
    amount = _number(value) / SHOPEE_CURRENCY_SCALE
    rounded = round(amount, 2)
    return int(rounded) if rounded.is_integer() else rounded


def _format_amount(value):
    number = _number(value)
    return f"{int(number):,}" if number.is_integer() else f"{number:,.2f}".rstrip("0").rstrip(".")


def _parse_shopee_api_voucher(raw_voucher, now_ts):
    percentage = _number(
        raw_voucher.get("discount_percentage")
        or raw_voucher.get("reward_percentage")
    )
    fixed_value = _shopee_money(
        raw_voucher.get("discount_value")
        or raw_voucher.get("reward_value")
    )
    coin_percentage = _number(raw_voucher.get("coin_percentage"))
    title_from_api = str(raw_voucher.get("title") or "").strip()
    is_shipping = bool(
        raw_voucher.get("fsv_voucher_card_ui_info")
        or raw_voucher.get("fsv_error_message")
    )
    is_coins = bool(
        coin_percentage
        or raw_voucher.get("coin_cap")
        or "coin" in title_from_api.lower()
    )

    if is_shipping:
        discount_type = "shipping"
        discount_value = fixed_value
        generated_title = f"ส่งฟรีสูงสุด ฿{_format_amount(fixed_value)}"
    elif is_coins:
        discount_type = "coins"
        discount_value = coin_percentage or percentage
        generated_title = f"รับ Coins คืน {_format_amount(discount_value)}%"
    elif percentage:
        discount_type = "percentage"
        discount_value = int(percentage) if percentage.is_integer() else percentage
        generated_title = f"ส่วนลด {_format_amount(discount_value)}%"
    else:
        discount_type = "fixed"
        discount_value = fixed_value
        generated_title = f"ส่วนลด ฿{_format_amount(discount_value)}"

    min_spend = _shopee_money(raw_voucher.get("min_spend"))
    cap_raw = (
        raw_voucher.get("discount_cap")
        or raw_voucher.get("reward_cap")
        or raw_voucher.get("coin_cap")
    )
    max_discount = _shopee_money(cap_raw)
    if not max_discount and discount_type in {"fixed", "shipping"}:
        max_discount = fixed_value

    claimed_pct = int(
        max(
            0,
            min(
                100,
                _number(
                    raw_voucher.get("percentage_used")
                    if raw_voucher.get("percentage_used") is not None
                    else raw_voucher.get("percentage_claimed")
                ),
            ),
        )
    )
    end_time = int(_number(raw_voucher.get("end_time")))
    expiry_days = max(0, math.ceil((end_time - now_ts) / 86400)) if end_time else 0
    expiry_info = (
        f"ใช้ได้ถึง: {time.strftime('%d.%m.%Y', time.localtime(end_time))}"
        if end_time
        else ""
    )
    scope = "สินค้าที่กำหนด" if raw_voucher.get("product_limit") else "ใช้ได้ทั้งร้าน"
    title = title_from_api or generated_title
    terms = (
        f"{title} | ขั้นต่ำ ฿{_format_amount(min_spend)}"
        f" | ลดสูงสุด ฿{_format_amount(max_discount)} | {scope}"
    )
    promotion_id = raw_voucher.get("promotionid") or raw_voucher.get("voucher_code")

    return {
        "voucher_id": str(promotion_id or ""),
        "code": str(raw_voucher.get("voucher_code") or ""),
        "title": f"🎟️ {title} ({scope})",
        "discount_type": discount_type,
        "discount_value": discount_value,
        "max_discount": max_discount,
        "min_spend": min_spend,
        "claimed_pct": claimed_pct,
        "expiry_days": expiry_days,
        "expiry_info": expiry_info,
        "expiry_timestamp": end_time,
        "start_timestamp": int(_number(raw_voucher.get("start_time"))),
        "terms": terms,
        "available": not any(
            bool(raw_voucher.get(flag))
            for flag in ("fully_redeemed", "has_expired", "disabled", "fully_claimed")
        ),
        "source": "shopee_api",
    }


def _parse_shopee_api_shop(
    shop_data, voucher_data, username, shop_url, category_id=""
):
    now_ts = time.time()
    raw_vouchers = voucher_data.get("voucher_list")
    if not isinstance(raw_vouchers, list):
        raise ShopeeVoucherFetchError("Shopee API ไม่มี voucher_list")

    vouchers = []
    seen = set()
    for raw_voucher in raw_vouchers:
        if not isinstance(raw_voucher, dict):
            continue
        parsed_voucher = _parse_shopee_api_voucher(raw_voucher, now_ts)
        identity = parsed_voucher["voucher_id"] or parsed_voucher["code"]
        if not identity or identity in seen:
            continue
        seen.add(identity)
        vouchers.append(parsed_voucher)

    portrait = str((shop_data.get("account") or {}).get("portrait") or "")
    is_official = bool(shop_data.get("show_official_shop_label"))
    category = (
        f"Shopee Category {category_id}" if category_id else "Uncategorized"
    )
    return {
        "shop_id": str(shop_data.get("shopid") or voucher_data.get("shopid") or ""),
        "shop_name": str(shop_data.get("name") or username),
        "shop_username": username,
        "category": category,
        "category_id": category_id,
        "badge": "Mall" if is_official else "Shopee",
        "rating": round(_number(shop_data.get("rating_star")), 2),
        "avatar": (
            f"https://down-th.img.susercontent.com/file/{portrait}" if portrait else ""
        ),
        "source": "shopee_api",
        "source_url": shop_url,
        "vouchers": vouchers,
    }


def fetch_shopee_shop_vouchers_web(url_or_username, raise_on_error=False):
    """
    Fetch every voucher returned for a Shopee storefront via Shopee's live APIs.

    The storefront HTML is JavaScript-rendered and can be replaced by a login
    page, so parsing the initial HTML is not a reliable live-data source.
    """
    try:
        username, shop_url = _resolve_shopee_shop_target(url_or_username)
        category_id = _extract_shopee_category_id(url_or_username)
        encoded_username = urllib.parse.quote(username, safe="")
        shop_payload = _fetch_shopee_json(
            f"{SHOPEE_API_BASE}/shop/get_shop_base?username={encoded_username}",
            shop_url,
        )
        shop_data = shop_payload.get("data")
        if not isinstance(shop_data, dict) or not shop_data.get("shopid"):
            raise ShopeeVoucherFetchError(f"ไม่พบร้าน Shopee @{username}")

        shop_id = int(shop_data["shopid"])
        voucher_payload = _fetch_shopee_json(
            (
                f"{SHOPEE_API_BASE}/voucher_wallet/"
                f"get_shop_vouchers_by_shopid?shopid={shop_id}"
            ),
            shop_url,
        )
        voucher_data = voucher_payload.get("data")
        if not isinstance(voucher_data, dict):
            raise ShopeeVoucherFetchError("Shopee API ไม่ส่งข้อมูล voucher ของร้าน")
        return _parse_shopee_api_shop(
            shop_data,
            voucher_data,
            username,
            shop_url,
            category_id=category_id,
        )
    except ShopeeVoucherFetchError as exc:
        if raise_on_error:
            raise
        print(f"Warning: {exc}", file=sys.stderr)
        return None

def fetch_active_shop_vouchers(force_refresh=False, category_filter=None, shop_filter=None, html_file=None, html_string=None, url=None):
    """
    Fetch and return list of shops that currently have active discount vouchers.
    Supports cache, category filtering, shop username filtering, and HTML parsing.
    """
    # 1. Direct HTML String Parsing
    if html_string:
        parsed = parse_shopee_html_vouchers(html_string)
        return {
            "scan_time": time.time(),
            "total_shops": 1 if parsed["vouchers"] else 0,
            "total_vouchers": len(parsed["vouchers"]),
            "from_cache": False,
            "shops": [parsed] if parsed["vouchers"] else []
        }
        
    # 2. Direct HTML File Parsing
    if html_file and os.path.exists(html_file):
        try:
            with open(html_file, "r", encoding="utf-8") as f:
                content = f.read()
            parsed = parse_shopee_html_vouchers(content)
            return {
                "scan_time": time.time(),
                "total_shops": 1 if parsed["vouchers"] else 0,
                "total_vouchers": len(parsed["vouchers"]),
                "from_cache": False,
                "shops": [parsed] if parsed["vouchers"] else []
            }
        except Exception as e:
            print(f"Error reading html_file {html_file}: {e}", file=sys.stderr)

    def apply_filters(payload):
        shops = list(payload.get("shops", []))
        if category_filter and category_filter.lower() != "all":
            category_needle = category_filter.casefold()
            shops = [
                shop
                for shop in shops
                if category_needle
                in {
                    str(shop.get("category", "")).casefold(),
                    str(shop.get("category_id", "")).casefold(),
                }
            ]
        if shop_filter and shop_filter.lower() != "all":
            needle = shop_filter.lower()
            shops = [
                shop
                for shop in shops
                if needle in str(shop.get("shop_username", "")).lower()
                or needle in str(shop.get("shop_name", "")).lower()
            ]
        filtered = dict(payload)
        filtered["shops"] = shops
        filtered["total_shops"] = len(shops)
        filtered["total_vouchers"] = sum(
            len(shop.get("vouchers", [])) for shop in shops
        )
        return filtered

    explicit_targets = url is not None
    if isinstance(url, (list, tuple, set)):
        targets = [str(target).strip() for target in url if str(target).strip()]
    elif url:
        targets = [str(url).strip()]
    else:
        targets = list(DEFAULT_SHOPEE_SHOP_URLS)

    if not explicit_targets and not force_refresh:
        cached = load_vouchers_cache()
        if cached and list(cached.get("targets", [])) == targets:
            cached_payload = dict(cached)
            cached_payload["from_cache"] = True
            return apply_filters(cached_payload)

    scan_time = time.time()
    result_shops = []
    errors = []
    seen_shops = set()
    for target in targets:
        try:
            parsed_shop = fetch_shopee_shop_vouchers_web(
                target, raise_on_error=True
            )
        except ShopeeVoucherFetchError as exc:
            errors.append({"target": target, "message": str(exc)})
            continue
        shop_identity = parsed_shop.get("shop_id") or parsed_shop.get("shop_username")
        if shop_identity in seen_shops:
            continue
        seen_shops.add(shop_identity)
        result_shops.append(parsed_shop)

    payload = {
        "scan_time": scan_time,
        "total_shops": len(result_shops),
        "total_vouchers": sum(
            len(shop.get("vouchers", [])) for shop in result_shops
        ),
        "from_cache": False,
        "source": "shopee_api",
        "targets": targets,
        "shops": result_shops,
    }
    if errors:
        payload["errors"] = errors
    if not explicit_targets and result_shops and not errors:
        save_vouchers_cache(payload)
    return apply_filters(payload)

def format_text(data):
    lines = []
    lines.append("🛍️ --- รายงานสรุปโค้ดส่วนลดหน้าร้านค้า (Shopee Vouchers) ---")
    lines.append(f"⏱️ เวลาที่สแกน: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(data['scan_time']))}")
    lines.append(f"🏪 จำนวนร้านค้าที่มีส่วนลด: {data['total_shops']} ร้าน | รวมส่วนลดทั้งหมด: {data['total_vouchers']} โค้ด")
    for error in data.get("errors", []):
        lines.append(f"⚠️ ดึงข้อมูลไม่สำเร็จ [{error['target']}]: {error['message']}")
    lines.append("=" * 60)
    
    for s in data["shops"]:
        lines.append(f"\n🏬 [{s['badge']}] {s['shop_name']} (@{s['shop_username']}) | หมวดหมู่: {s['category']}")
        for v in s["vouchers"]:
            pct = v.get('claimed_pct', 0)
            pct_bar = f"[{'█' * (pct // 10)}{'░' * (10 - pct // 10)}] {pct}%"
            exp_info = f" | {v['expiry_info']}" if v.get('expiry_info') else f" | หมดอายุใน {v['expiry_days']} วัน"
            lines.append(f"  🎟️ โค้ด: {v['code']} — {v['title']}")
            lines.append(f"     • เงื่อนไข: ซื้อขั้นต่ำ ฿{v['min_spend']:,} (ลดสูงสุด ฿{v['max_discount']:,})")
            lines.append(f"     • เก็บไปแล้ว: {pct_bar}{exp_info}")
    lines.append("\n" + "=" * 60)
    return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser(description="Shopee Storefront Voucher Scanner & Parser", allow_abbrev=False)
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    parser.add_argument(
        "--category",
        type=str,
        default="all",
        help="Category name or Shopee categoryId (e.g. 100637)",
    )
    parser.add_argument("--shop", type=str, default="all", help="Filter by shop username (e.g. yuedpao_official)")
    parser.add_argument("--html-file", type=str, default=None, help="Path to Shopee storefront HTML file to extract vouchers")
    parser.add_argument("--html-string", type=str, default=None, help="Raw Shopee HTML string to extract vouchers")
    parser.add_argument(
        "--url",
        action="append",
        default=None,
        help="Shopee shop URL/username to fetch; repeat for multiple shops",
    )
    parser.add_argument("--force-refresh", action="store_true", help="Bypass cache and force rescan")
    args = parser.parse_args()

    data = fetch_active_shop_vouchers(
        force_refresh=args.force_refresh,
        category_filter=args.category,
        shop_filter=args.shop,
        html_file=args.html_file,
        html_string=args.html_string,
        url=args.url
    )
    
    if args.format == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(format_text(data))

if __name__ == "__main__":
    main()
