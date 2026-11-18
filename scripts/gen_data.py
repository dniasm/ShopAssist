"""Generate the ShopAssist v2 seed data into data/seed/v2/.

Usage (from the repo root):
    python scripts/gen_data.py

Stdlib only, no network. Every random choice comes from random.Random(SEED) and every
timestamp is relative to the fixed TODAY, so running this twice writes byte-identical files.
Run scripts/validate_data.py afterwards.

Files written (JSON, UTF-8, 2-space indent, trailing newline):
    catalog.json    {"products": [...], "skus": [...]}
    inventory.json  [{sku_id, product_id, on_hand}]          one row per SKU
    customers.json  {"users": [{user_id, name, email, city}]}
    orders.json     [order]   order = {order_id, user_id, status, lines, subtotal_cents,
                               shipping_cents, total_cents, created_at, updated_at, events}
                    line = {sku_id, product_id, name, size, color, quantity,
                            unit_price_cents, line_total_cents}
    payments.json   [{payment_id, order_id, amount_cents, status}]   one per order

Random streams: SEED drives everything structural (brand, type, sizes, colours, prices,
ratings, dates, inventory, orders). Names and descriptions come from a separate stream per
product, random.Random(f"text:{product_id}"), so text can change without touching data.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "data" / "seed" / "v2"

SEED = 42
# Fixed "today" so timestamps never depend on the wall clock.
TODAY = datetime(2026, 11, 18, tzinfo=timezone.utc)
HISTORY_START = datetime(2025, 5, 18, tzinfo=timezone.utc)  # about 18 months back

PRODUCTS_PER_CATEGORY = 40  # 10 categories -> 400 products
PALETTE = ["black", "white", "grey", "navy", "blue", "green", "olive", "red",
           "burgundy", "beige", "brown", "pink", "yellow", "orange"]
TOP_SIZES = ["XS", "S", "M", "L", "XL", "XXL"]
JEAN_SIZES = ["28", "30", "32", "34", "36"]
SHOE_SIZES = ["6", "7", "8", "9", "10", "11", "12"]
ONE_SIZE = ["ONE"]

# Invented brands only.
BRANDS = [
    "Brevlan", "Corvane", "Dunmoor", "Ellistra", "Fenrowe", "Gallowmere", "Hartwick Lane",
    "Ivorside", "Jastrel", "Kelvane", "Lorrimere", "Mirelle Ash", "Nordhaven", "Orlinde",
    "Pellworth", "Quinnell", "Rhydwen", "Strathvale", "Tessaly", "Umbrarin", "Vantesk",
    "Wrenfield", "Yarrowby", "Zephyrine", "Ostrava Keel",
]

# Name adjectives. None of them clashes with a type noun (no "Studio", no "Field").
ADJECTIVES = [
    "Classic", "Relaxed", "Everyday", "Slim", "Lightweight", "Heritage", "Urban", "Minimal",
    "Trail", "Essential", "Weekend", "Modern", "Rugged", "Soft", "Compact", "Signature",
    "Bold", "Tailored", "Refined", "Honest", "Clean", "Easy", "Vintage", "Sharp", "Fluid",
    "Crisp", "Supple", "Daily", "Nomad", "Coastal", "Alpine", "Quiet", "Plain", "Robust",
    "Sleek", "Airy",
]

# Each product TYPE has its own materials, features, uses and whether it may claim waterproofing.
# The type name is the noun in the product name. Keep the order: product types are drawn in this order.
TYPES = {
    "headphones": {
        "Over-Ear Headphones": {
            "waterproof": False,
            "materials": ["memory-foam earcups", "machined aluminium", "soft-touch polymer"],
            "features": ["30-hour battery life", "active noise cancellation", "a detachable boom mic", "a compact carry case"],
            "uses": ["long commutes", "focused work at a desk", "travel and long flights", "late-night listening"],
        },
        "On-Ear Headphones": {
            "waterproof": False,
            "materials": ["recycled plastic", "brushed-steel headband", "soft-touch polymer"],
            "features": ["multipoint Bluetooth pairing", "a foldable headband", "a lightweight frame"],
            "uses": ["everyday listening", "commuting by train", "working from a café", "watching films at home"],
        },
        "Wireless Earbuds": {
            "waterproof": True,
            "materials": ["silicone ear tips", "soft-touch polymer", "recycled plastic"],
            "features": ["a low-latency wireless link", "a charging case", "touch controls"],
            "uses": ["gym sessions", "running outdoors", "commuting by bus", "quiet train journeys"],
        },
        "Noise-Cancelling Headphones": {
            "waterproof": False,
            "materials": ["memory-foam earcups", "machined aluminium", "braided cable"],
            "features": ["active noise cancellation", "30-hour battery life", "a transparency mode"],
            "uses": ["long-haul flights", "busy open-plan offices", "focused study sessions", "long commutes"],
        },
        "Sport Earbuds": {
            "waterproof": True,
            "materials": ["silicone ear tips", "soft-touch polymer", "recycled plastic"],
            "features": ["a secure over-ear hook", "a sweat-resistant shell", "a charging case"],
            "uses": ["running outdoors", "gym sessions", "cycling to work", "long runs"],
        },
        "Studio Headphones": {
            "waterproof": False,
            "materials": ["braided cable", "machined aluminium", "memory-foam earcups"],
            "features": ["a flat frequency response", "replaceable ear pads", "a detachable cable"],
            "uses": ["mixing and mastering", "recording sessions", "critical listening at home"],
        },
        "Foldable Headphones": {
            "waterproof": False,
            "materials": ["recycled plastic", "soft-touch polymer", "brushed steel"],
            "features": ["a folding headband", "a compact carry pouch", "a slim profile"],
            "uses": ["weekend trips", "commuting by train", "long walks with music"],
        },
    },
    "hoodies": {
        "Pullover Hoodie": {
            "waterproof": False,
            "materials": ["brushed cotton fleece", "organic cotton", "heavyweight French terry"],
            "features": ["a kangaroo pocket", "a drawstring hood", "ribbed cuffs and hem"],
            "uses": ["cold-weather walks", "lounging at home", "weekend errands"],
        },
        "Zip Hoodie": {
            "waterproof": False,
            "materials": ["cotton-polyester blend", "brushed cotton fleece", "organic cotton"],
            "features": ["a full-length zip", "two side pockets", "a soft interior lining"],
            "uses": ["layering under a jacket", "commuting on chilly mornings", "weekend errands"],
        },
        "Oversized Hoodie": {
            "waterproof": False,
            "materials": ["heavyweight French terry", "bamboo-cotton jersey", "organic cotton"],
            "features": ["a relaxed, roomy fit", "dropped shoulders", "a double-layered hood"],
            "uses": ["lounging at home", "relaxed streetwear days", "cold-weather walks"],
        },
        "Cropped Hoodie": {
            "waterproof": False,
            "materials": ["bamboo-cotton jersey", "cotton-polyester blend", "brushed cotton fleece"],
            "features": ["a short, cropped body", "a snug fit", "a ribbed hem"],
            "uses": ["layering over a tee", "casual nights out", "warm-weather evenings"],
        },
        "Heavyweight Hoodie": {
            "waterproof": False,
            "materials": ["heavyweight French terry", "brushed cotton fleece", "organic cotton"],
            "features": ["a thick, structured hood", "reinforced seams", "a kangaroo pocket"],
            "uses": ["training days at the gym", "cold-weather walks", "outdoor weekends"],
        },
        "Half-Zip Hoodie": {
            "waterproof": False,
            "materials": ["brushed cotton fleece", "cotton-polyester blend", "bamboo-cotton jersey"],
            "features": ["a half-length zip", "a stand-up collar", "thumbholes in the cuffs"],
            "uses": ["training days at the gym", "outdoor weekends", "layering under a jacket"],
        },
    },
    "t-shirts": {
        "Crew-Neck T-Shirt": {
            "waterproof": False,
            "materials": ["combed cotton jersey", "pima cotton", "organic cotton"],
            "features": ["a ribbed crew neck", "a tagless label", "a boxy fit"],
            "uses": ["everyday wear", "layering under overshirts", "casual office days"],
        },
        "V-Neck T-Shirt": {
            "waterproof": False,
            "materials": ["combed cotton jersey", "modal blend", "pima cotton"],
            "features": ["a deep V-neck", "a tailored slim cut", "a tagless label"],
            "uses": ["summer days", "casual office days", "layering under a knit"],
        },
        "Graphic Tee": {
            "waterproof": False,
            "materials": ["slub cotton", "combed cotton jersey", "organic cotton"],
            "features": ["a screen-printed front graphic", "a relaxed, boxy fit", "a garment-dyed finish"],
            "uses": ["weekend outings", "festival days", "everyday wear"],
        },
        "Pocket T-Shirt": {
            "waterproof": False,
            "materials": ["combed cotton jersey", "slub cotton", "lightweight linen blend"],
            "features": ["a chest pocket", "a ribbed collar", "a regular fit"],
            "uses": ["casual office days", "everyday wear", "summer days"],
        },
        "Longline T-Shirt": {
            "waterproof": False,
            "materials": ["modal blend", "pima cotton", "combed cotton jersey"],
            "features": ["an extended hem", "a longer body length", "a soft hand feel"],
            "uses": ["layering under overshirts", "summer days", "warm-weather travel"],
        },
        "Henley T-Shirt": {
            "waterproof": False,
            "materials": ["lightweight linen blend", "slub cotton", "organic cotton"],
            "features": ["a three-button placket", "a grandad collar", "a relaxed fit"],
            "uses": ["warm-weather travel", "casual weekends", "summer days"],
        },
    },
    "jackets": {
        "Rain Jacket": {
            "waterproof": True,
            "materials": ["ripstop nylon", "recycled polyester shell"],
            "features": ["a packable hood", "taped seams", "a storm flap"],
            "uses": ["city commutes in changeable weather", "rainy walks", "travel and airport transfers"],
        },
        "Bomber Jacket": {
            "waterproof": False,
            "materials": ["recycled polyester shell", "wool-blend", "ripstop nylon"],
            "features": ["two zip pockets", "a ribbed collar and cuffs", "a quilted lining"],
            "uses": ["cool spring evenings", "casual nights out", "city commutes in changeable weather"],
        },
        "Waxed Field Jacket": {
            "waterproof": False,
            "materials": ["waxed cotton", "heavy waxed twill"],
            "features": ["a storm flap", "two zip pockets", "an adjustable hem"],
            "uses": ["hiking and camping", "weekend trail walks", "cold mornings in the countryside"],
        },
        "Puffer Jacket": {
            "waterproof": False,
            "materials": ["insulated recycled fill", "recycled polyester shell", "ripstop nylon"],
            "features": ["a quilted lining", "a stand-up collar", "an adjustable hem"],
            "uses": ["cold mornings and evenings", "winter city walks", "travel and airport transfers"],
        },
        "Trucker Jacket": {
            "waterproof": False,
            "materials": ["recycled polyester shell", "cotton twill", "wool-blend"],
            "features": ["an adjustable hem", "two chest pockets", "a button front"],
            "uses": ["relaxed city days", "weekend errands", "layering over a hoodie"],
        },
        "Softshell Jacket": {
            "waterproof": False,
            "materials": ["ripstop nylon", "recycled polyester shell", "wool-blend"],
            "features": ["a stretch fabric", "a stand-up collar", "taped seams"],
            "uses": ["hiking and camping", "weekend trail walks", "cool, windy days outdoors"],
        },
    },
    "jeans": {
        "Straight-Leg Jeans": {
            "waterproof": False,
            "materials": ["stretch denim", "organic cotton denim", "rigid 12oz denim"],
            "features": ["a mid rise", "a five-pocket cut", "a stretch waistband"],
            "uses": ["everyday wear", "office-casual dressing", "weekend outings"],
        },
        "Slim-Fit Jeans": {
            "waterproof": False,
            "materials": ["stretch denim", "recycled cotton denim", "organic cotton denim"],
            "features": ["a slim cut through the leg", "a stretch waistband", "a faded wash"],
            "uses": ["evenings out", "office-casual dressing", "weekend outings"],
        },
        "Tapered Jeans": {
            "waterproof": False,
            "materials": ["stretch denim", "recycled cotton denim", "rigid 12oz denim"],
            "features": ["a tapered leg", "a mid rise", "copper rivets"],
            "uses": ["everyday wear", "layering in autumn", "smart-casual outfits"],
        },
        "Relaxed Jeans": {
            "waterproof": False,
            "materials": ["recycled cotton denim", "organic cotton denim", "rigid 12oz denim"],
            "features": ["a roomy seat and thigh", "a brushed interior", "a faded wash"],
            "uses": ["long days on your feet", "weekend outings", "everyday wear"],
        },
        "Bootcut Jeans": {
            "waterproof": False,
            "materials": ["stretch denim", "rigid 12oz denim", "recycled cotton denim"],
            "features": ["a flared hem", "a five-pocket cut", "a high rise"],
            "uses": ["office-casual dressing", "layering in autumn", "everyday wear"],
        },
        "Selvedge Jeans": {
            "waterproof": False,
            "materials": ["selvedge denim", "rigid 12oz denim", "organic cotton denim"],
            "features": ["a selvedge hem", "copper rivets", "a raw indigo finish"],
            "uses": ["weekend outings", "everyday wear", "long days on your feet"],
        },
    },
    "shoes": {
        "Leather Sneakers": {
            "waterproof": False,
            "materials": ["full-grain leather", "smooth calfskin", "vegetable-tanned leather"],
            "features": ["a cushioned insole", "a low-profile rubber sole", "a padded collar"],
            "uses": ["city walking", "office wear", "smart-casual outfits"],
        },
        "Suede Chelsea Boots": {
            "waterproof": False,
            "materials": ["suede", "rubber-soled nubuck", "smooth calfskin"],
            "features": ["an elasticated side panel", "a leather heel tab", "a cushioned insole"],
            "uses": ["office wear", "city walking", "smart-casual outfits"],
        },
        "Canvas Trainers": {
            "waterproof": False,
            "materials": ["organic canvas", "recycled canvas", "vulcanised rubber"],
            "features": ["a vulcanised rubber sole", "a lace-up closure", "a breathable upper"],
            "uses": ["city walking", "everyday wear", "casual weekends"],
        },
        "Low-Top Trainers": {
            "waterproof": False,
            "materials": ["recycled knit mesh", "full-grain leather", "organic canvas"],
            "features": ["a grippy rubber outsole", "a padded collar", "a breathable upper"],
            "uses": ["gym and training", "city walking", "everyday wear"],
        },
        "Derby Shoes": {
            "waterproof": False,
            "materials": ["full-grain leather", "smooth calfskin", "polished leather"],
            "features": ["a lace-up closure", "a leather lining", "a stacked leather heel"],
            "uses": ["office wear", "formal occasions", "smart-casual outfits"],
        },
        "Hiking Boots": {
            "waterproof": True,
            "materials": ["full-grain leather", "oiled nubuck", "recycled rubber"],
            "features": ["a grippy lug outsole", "an ankle-height support collar", "a breathable lining"],
            "uses": ["light trail hikes", "country walks", "rainy weekend hikes"],
        },
        "Running Shoes": {
            "waterproof": False,
            "materials": ["recycled knit mesh", "engineered textile upper", "recycled rubber"],
            "features": ["a cushioned midsole", "a breathable upper", "a secure lace-up fit"],
            "uses": ["running outdoors", "gym and training", "daily jogging"],
        },
    },
    "backpacks": {
        "Daypack": {
            "waterproof": True,
            "materials": ["500D recycled nylon", "ballistic polyester", "TPU-coated fabric"],
            "features": ["a quick-access top pocket", "a water bottle pocket", "a hidden back pocket"],
            "uses": ["day hikes", "daily commuting", "weekend city breaks"],
        },
        "Commuter Backpack": {
            "waterproof": True,
            "materials": ["ballistic polyester", "500D recycled nylon", "TPU-coated fabric"],
            "features": ["a padded 16-inch laptop sleeve", "a magnetic clasp", "a hidden back pocket"],
            "uses": ["daily commuting", "university lectures", "carrying a laptop to work"],
        },
        "Laptop Backpack": {
            "waterproof": False,
            "materials": ["500D recycled nylon", "ballistic polyester", "leather-trimmed canvas"],
            "features": ["a padded 16-inch laptop sleeve", "a separate tablet pocket", "a quick-access top pocket"],
            "uses": ["daily commuting", "university lectures", "business travel"],
        },
        "Roll-Top Backpack": {
            "waterproof": True,
            "materials": ["waxed canvas", "TPU-coated fabric", "ballistic polyester"],
            "features": ["a roll-top closure", "hip-belt straps", "a water bottle pocket"],
            "uses": ["overnight trips", "day hikes", "cycling commutes"],
        },
        "Hiking Rucksack": {
            "waterproof": True,
            "materials": ["ballistic polyester", "500D recycled nylon", "TPU-coated fabric"],
            "features": ["hip-belt straps", "a padded back panel", "a hydration sleeve"],
            "uses": ["day hikes", "overnight trips", "trekking in the hills"],
        },
        "Weekender Backpack": {
            "waterproof": False,
            "materials": ["leather-trimmed canvas", "waxed canvas", "500D recycled nylon"],
            "features": ["a magnetic clasp", "a padded shoulder strap", "a roomy main compartment"],
            "uses": ["overnight trips", "weekend city breaks", "carrying gym kit"],
        },
    },
    "watches": {
        "Chronograph Watch": {
            "waterproof": True,
            "materials": ["stainless steel", "brushed aluminium case", "sapphire-glass crystal"],
            "features": ["a date window", "a screw-down crown", "a stopwatch function"],
            "uses": ["office meetings", "formal occasions", "weekend sports"],
        },
        "Field Watch": {
            "waterproof": False,
            "materials": ["brushed aluminium case", "stainless steel", "bio-resin strap"],
            "features": ["luminous hands", "a leather strap", "a date window"],
            "uses": ["outdoor adventures", "everyday wear", "weekend sports"],
        },
        "Dive-Style Watch": {
            "waterproof": True,
            "materials": ["stainless steel", "titanium case", "bio-resin strap"],
            "features": ["a screw-down crown", "a rotating bezel", "luminous hands"],
            "uses": ["weekend sports", "outdoor adventures", "everyday wear"],
        },
        "Dress Watch": {
            "waterproof": False,
            "materials": ["stainless steel", "sapphire-glass crystal", "titanium case"],
            "features": ["a slim case", "a leather strap", "a simple dial"],
            "uses": ["formal occasions", "office meetings", "evening events"],
        },
        "Minimalist Watch": {
            "waterproof": False,
            "materials": ["brushed aluminium case", "sapphire-glass crystal", "bio-resin strap"],
            "features": ["a simple dial", "a mesh bracelet", "a slim profile"],
            "uses": ["everyday wear", "office meetings", "casual weekends"],
        },
        "Sport Watch": {
            "waterproof": True,
            "materials": ["bio-resin strap", "titanium case", "stainless steel"],
            "features": ["a 100m water-resistance rating", "a mesh bracelet", "a backlit display"],
            "uses": ["running outdoors", "gym sessions", "weekend sports"],
        },
    },
    "sunglasses": {
        "Aviator Sunglasses": {
            "waterproof": False,
            "materials": ["stainless steel frames", "titanium frames"],
            "features": ["polarised lenses", "UV400 protection", "a spring hinge"],
            "uses": ["driving on sunny days", "city walks", "beach trips"],
        },
        "Round Sunglasses": {
            "waterproof": False,
            "materials": ["bio-acetate frames", "titanium frames"],
            "features": ["mirrored lenses", "a lightweight wire frame", "UV400 protection"],
            "uses": ["city walks", "festival weekends", "beach trips"],
        },
        "Wayfarer Sunglasses": {
            "waterproof": False,
            "materials": ["bio-acetate frames", "TR90 nylon frames"],
            "features": ["polarised lenses", "a scratch-resistant coating", "UV400 protection"],
            "uses": ["driving on sunny days", "everyday wear", "city walks"],
        },
        "Sport Sunglasses": {
            "waterproof": False,
            "materials": ["TR90 nylon frames", "bio-acetate frames"],
            "features": ["a lightweight wraparound shape", "a grip-friendly nose pad", "mirrored lenses"],
            "uses": ["cycling and running", "beach trips", "driving on sunny days"],
        },
        "Square Sunglasses": {
            "waterproof": False,
            "materials": ["bio-acetate frames", "stainless steel frames"],
            "features": ["a bold square frame", "polarised lenses", "a spring hinge"],
            "uses": ["festival weekends", "city walks", "everyday wear"],
        },
        "Clubmaster Sunglasses": {
            "waterproof": False,
            "materials": ["bio-acetate frames", "stainless steel frames"],
            "features": ["a classic browline", "a spring hinge", "UV400 protection"],
            "uses": ["city walks", "everyday wear", "driving on sunny days"],
        },
    },
    "accessories": {
        "Leather Belt": {
            "waterproof": False,
            "materials": ["full-grain leather", "brass-finished metal", "vegetable-tanned leather"],
            "features": ["a brass pin buckle", "a minimal debossed finish", "a row of adjustment holes"],
            "uses": ["smart-casual outfits", "daily carry", "office wear"],
        },
        "Canvas Cap": {
            "waterproof": False,
            "materials": ["cotton canvas", "recycled nylon", "organic cotton"],
            "features": ["an adjustable strap", "a curved brim", "a soft-touch lining"],
            "uses": ["sunny days outdoors", "casual weekends", "travel"],
        },
        "Merino Beanie": {
            "waterproof": False,
            "materials": ["merino wool", "recycled wool blend", "cashmere-blend yarn"],
            "features": ["a ribbed knit", "a soft-touch lining", "a folded-edge trim"],
            "uses": ["cold weather", "winter commutes", "travel"],
        },
        "Bifold Wallet": {
            "waterproof": False,
            "materials": ["full-grain leather", "smooth calfskin", "recycled nylon"],
            "features": ["RFID-blocking slots", "a slim profile", "a coin pocket"],
            "uses": ["daily carry", "travel", "gifting"],
        },
        "Silk Scarf": {
            "waterproof": False,
            "materials": ["silk twill", "recycled silk blend", "cotton voile"],
            "features": ["a hand-rolled edge", "a soft drape", "a printed pattern"],
            "uses": ["smart-casual outfits", "gifting", "evening events"],
        },
        "Cardholder": {
            "waterproof": False,
            "materials": ["cork", "full-grain leather", "recycled nylon"],
            "features": ["a slim profile", "RFID-blocking slots", "a minimal debossed finish"],
            "uses": ["daily carry", "travel", "gifting"],
        },
        "Tote Bag": {
            "waterproof": False,
            "materials": ["cotton canvas", "waxed canvas", "recycled nylon"],
            "features": ["reinforced handles", "an internal zip pocket", "a roomy main compartment"],
            "uses": ["daily carry", "grocery runs", "travel"],
        },
    },
}

# Size and price settings per category. Product types come from TYPES.
CATEGORIES = {
    "headphones": {"size_kind": "one", "tiers": [(1499, 9999), (1499, 9999), (10000, 29999)]},
    "hoodies": {"size_kind": "top", "tiers": [(3900, 8900)]},
    "t-shirts": {"size_kind": "top", "tiers": [(1499, 3499)]},
    "jackets": {"size_kind": "top", "tiers": [(7900, 29900)]},
    "jeans": {"size_kind": "jeans", "tiers": [(4900, 12900)]},
    "shoes": {"size_kind": "shoes", "tiers": [(5900, 15900)]},
    "backpacks": {"size_kind": "one", "tiers": [(2900, 15900)]},
    "watches": {"size_kind": "one", "tiers": [(4900, 49900)]},
    "sunglasses": {"size_kind": "one", "tiers": [(1900, 19900)]},
    "accessories": {"size_kind": "one", "tiers": [(499, 7900)]},
}

# --- Frozen draw shape of the first generation -------------------------------------------
# The structural stream must consume exactly the draws it consumed before, so prices, sizes,
# colours, inventory and orders stay identical. The old text code used these draws; they are
# now consumed without their results. Only these lengths matter, not the old wording.
LEGACY_ADJECTIVE_COUNT = 17
LEGACY_WATERPROOF_LINE_COUNT = 2
LEGACY_GENERIC_LINE_COUNT = 5
# category: (materials, features, uses, waterproof flag)
LEGACY_TEXT_SHAPE = {
    "headphones": (6, 6, 6, True),
    "hoodies": (5, 6, 5, False),
    "t-shirts": (5, 5, 5, False),
    "jackets": (5, 6, 5, True),
    "jeans": (5, 6, 5, False),
    "shoes": (5, 6, 5, True),
    "backpacks": (5, 6, 5, True),
    "watches": (5, 6, 5, True),
    "sunglasses": (4, 6, 5, False),
    "accessories": (6, 6, 5, False),
}

USERS = [
    ("U-01", "Priya Raman", "priya.raman@example.com", "Leeds"),
    ("U-02", "Tomas Lindqvist", "tomas.lindqvist@example.com", "Gothenburg"),
    ("U-03", "Hana Mori", "hana.mori@example.com", "Osaka"),
    ("U-04", "Daniel Osei", "daniel.osei@example.com", "Accra"),
    ("U-05", "Lucia Ferrand", "lucia.ferrand@example.com", "Lyon"),
    ("U-06", "Samuel Adeyemi", "samuel.adeyemi@example.com", "Ibadan"),
]

# Status path each historic order walks through, in time order.
STATUS_PATHS = {
    "CONFIRMED": ["CONFIRMED"],
    "PACKED": ["CONFIRMED", "PACKED"],
    "SHIPPED": ["CONFIRMED", "PACKED", "SHIPPED"],
    "DELIVERED": ["CONFIRMED", "PACKED", "SHIPPED", "OUT_FOR_DELIVERY", "DELIVERED"],
    "CANCELLED": ["CONFIRMED", "CANCELLED"],
}
STATUS_WEIGHTS = {"DELIVERED": 35, "SHIPPED": 20, "PACKED": 15, "CONFIRMED": 15, "CANCELLED": 15}
EVENT_MESSAGES = {
    "CONFIRMED": "Order confirmed and payment received.",
    "PACKED": "Items packed at the warehouse.",
    "SHIPPED": "Parcel handed to the carrier.",
    "OUT_FOR_DELIVERY": "Parcel is out for delivery.",
    "DELIVERED": "Parcel delivered.",
    "CANCELLED": "Order cancelled by the customer; payment refunded.",
}

# --- Description frames ------------------------------------------------------------------
# Every frame has a verb. Placeholders: {NP} capitalised noun phrase, {np} lowercase noun phrase,
# {M} material, {F} feature, {U} use, {PRON}, {BE}, {HAVE}, {FEAT}, {KEEP}, {SUIT}, {WORK},
# {OFFER}, {COME}. Plural and singular forms are supplied by agreement tables below.
S1_FRAMES = [
    "{NP} {BE} built from {M} and {HAVE} {F}.",
    "{NP} {BE} made from {M} and {FEAT} {F}.",
    "{NP} {BE} crafted with {M} and {HAVE} {F}.",
    "Built from {M}, {np} {KEEP} {F}.",
    "Made from {M}, {np} {FEAT} {F}.",
    "{NP} {HAVE} {F} and {BE} made from {M}.",
]
S2_FRAMES = [
    "{PRON} {SUIT} {U}.",
    "{PRON} {BE} designed for {U}.",
    "{PRON} {WORK} well for {U}.",
    "{PRON} {BE} built for {U}.",
    "{PRON} {OFFER} reliable performance for {U}.",
]
WATERPROOF_FRAMES = [
    "{PRON} {BE} fully waterproof, so rain and puddles are no problem.",
    "A fully waterproof finish keeps out rain and puddles.",
    "{PRON} {HAVE} a fully waterproof build for wet weather.",
    "The fully waterproof construction stays dry in wet weather.",
]
EXTRA_FRAMES = [
    "{PRON} {COME} in colours that suit most wardrobes.",
    "Reinforced stitching at the stress points keeps it durable.",
    "{PRON} {WORK} as a versatile everyday staple.",
    "{PRON} {WORK} just as well dressed up as dressed down.",
    "{PRON} {BE} easy to care for and built to last.",
]
SINGULAR_FORMS = {"BE": "is", "HAVE": "has", "FEAT": "features", "KEEP": "keeps", "SUIT": "suits",
                  "WORK": "works", "OFFER": "offers", "COME": "comes", "PRON": "It", "DET": "this"}
PLURAL_FORMS = {"BE": "are", "HAVE": "have", "FEAT": "feature", "KEEP": "keep", "SUIT": "suit",
                "WORK": "work", "OFFER": "offer", "COME": "come", "PRON": "They", "DET": "these"}


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _consume(rng: random.Random, n: int) -> None:
    """Consume the draw that rng.choice() on a length-n sequence would consume. The value is discarded."""
    rng.randrange(n)


def _consume_legacy_description(rng: random.Random, category: str) -> None:
    n_mat, n_feat, n_use, legacy_wp = LEGACY_TEXT_SHAPE[category]
    _consume(rng, n_mat)
    _consume(rng, n_feat)
    _consume(rng, n_use)
    roll = rng.random()
    if legacy_wp and roll < 0.35:
        _consume(rng, LEGACY_WATERPROOF_LINE_COUNT)
    elif roll < 0.6:
        _consume(rng, LEGACY_GENERIC_LINE_COUNT)


def sizes_for(rng: random.Random, kind: str) -> list[str]:
    full = {"top": TOP_SIZES, "jeans": JEAN_SIZES, "shoes": SHOE_SIZES}.get(kind)
    if full is None:
        return list(ONE_SIZE)
    if rng.random() < 0.5:
        return list(full)
    count = rng.randint(3, len(full) - 1)
    start = rng.randint(0, len(full) - count)
    return full[start:start + count]


def colours_for(rng: random.Random, force_blue_family: bool) -> list[str]:
    chosen = rng.sample(PALETTE, rng.randint(1, 4))
    if force_blue_family and not any(c in ("navy", "blue") for c in chosen):
        chosen[0] = rng.choice(["navy", "blue"])  # nothing in chosen is navy/blue, so no duplicate
    return sorted(chosen, key=PALETTE.index)


def describe(rng: random.Random, type_spec: dict, noun: str) -> str:
    plural = noun.endswith("s")
    forms = dict(PLURAL_FORMS if plural else SINGULAR_FORMS)
    det = forms.pop("DET")
    noun_lc = noun.lower()
    fields = dict(forms,
                  NP=f"{det.capitalize()} {noun_lc}",
                  np=f"{det} {noun_lc}",
                  M=rng.choice(type_spec["materials"]),
                  F=rng.choice(type_spec["features"]),
                  U=rng.choice(type_spec["uses"]))
    sentences = [
        rng.choice(S1_FRAMES).format_map(fields),
        rng.choice(S2_FRAMES).format_map(fields),
    ]
    roll = rng.random()
    if type_spec["waterproof"] and roll < 0.35:
        sentences.append(rng.choice(WATERPROOF_FRAMES).format_map(fields))
    elif roll < 0.6:
        sentences.append(rng.choice(EXTRA_FRAMES).format_map(fields))
    return " ".join(sentences)


def make_text(pid: str, brand: str, category: str, noun: str, used_names: set[str]) -> tuple[str, str]:
    """Name and description for one product, from its own random stream."""
    text_rng = random.Random(f"text:{pid}")
    adjective = text_rng.choice(ADJECTIVES)
    base = f"{brand} {adjective} {noun}"
    name, n = base, 2
    while name in used_names:
        name = f"{base} {n}"
        n += 1
    used_names.add(name)
    return name, describe(text_rng, TYPES[category][noun], noun)


def build_products(rng: random.Random) -> list[dict]:
    drafts = []
    span_minutes = int((TODAY - HISTORY_START).total_seconds() // 60)
    for category, spec in CATEGORIES.items():
        nouns = list(TYPES[category])
        for i in range(PRODUCTS_PER_CATEGORY):
            brand = rng.choice(BRANDS)
            _consume(rng, LEGACY_ADJECTIVE_COUNT)  # draw slot kept for the frozen stream; text uses its own
            noun = rng.choice(nouns)
            sizes = sizes_for(rng, spec["size_kind"])
            colours = colours_for(rng, force_blue_family=(category == "hoodies" and i < 8))
            low, high = rng.choice(spec["tiers"])
            price_cents = rng.randint(low // 100, high // 100) * 100 + rng.choice((99, 0))
            rating = round(rng.uniform(2.8, 4.9), 1)
            rating_count = int(2500 * rng.random() ** 3)  # skewed low, 0..2500
            created = HISTORY_START + timedelta(minutes=rng.randrange(span_minutes))
            _consume_legacy_description(rng, category)  # draw slots kept for the frozen stream
            drafts.append({
                "category": category,
                "brand": brand,
                "noun": noun,
                "price_cents": price_cents,
                "rating": rating,
                "rating_count": rating_count,
                "colors": colours,
                "sizes": sizes,
                "created_at": iso(created),
            })
    rng.shuffle(drafts)

    products = []
    used_names: set[str] = set()
    for i, draft in enumerate(drafts):
        pid = f"P-{1001 + i}"
        name, description = make_text(pid, draft["brand"], draft["category"], draft["noun"], used_names)
        products.append({
            "product_id": pid,
            "name": name,
            "category": draft["category"],
            "brand": draft["brand"],
            "description": description,
            "price_cents": draft["price_cents"],
            "rating": draft["rating"],
            "rating_count": draft["rating_count"],
            "colors": draft["colors"],
            "sizes": draft["sizes"],
            "created_at": draft["created_at"],
        })
    return products


def build_skus(products: list[dict]) -> list[dict]:
    skus = []
    for product in products:
        for size in product["sizes"]:
            for colour in product["colors"]:
                skus.append({
                    "sku_id": f"SKU-{100001 + len(skus)}",
                    "product_id": product["product_id"],
                    "size": size,
                    "color": colour,
                })
    return skus


def build_inventory(rng: random.Random, skus: list[dict]) -> list[dict]:
    rows = []
    for sku in skus:
        roll = rng.random()
        if roll < 0.05:
            on_hand = 0
        elif roll < 0.15:
            on_hand = rng.randint(1, 4)
        else:
            on_hand = rng.randint(5, 60)
        rows.append({"sku_id": sku["sku_id"], "product_id": sku["product_id"], "on_hand": on_hand})
    return rows


def build_orders(rng: random.Random, products: list[dict], skus: list[dict]):
    by_product = {p["product_id"]: p for p in products}
    # Latest order start leaves room for the longest status path (about 6 days) before TODAY.
    span_minutes = int((TODAY - timedelta(days=10) - HISTORY_START).total_seconds() // 60)
    drafts = []
    statuses = list(STATUS_WEIGHTS)
    weights = [STATUS_WEIGHTS[s] for s in statuses]
    for user_id, _name, _email, _city in USERS:
        for _ in range(rng.randint(2, 6)):
            status = rng.choices(statuses, weights=weights)[0]
            picked = rng.sample(skus, rng.choice([1, 1, 2, 2, 3]))
            lines = []
            for sku in picked:
                product = by_product[sku["product_id"]]
                quantity = rng.choice([1, 1, 1, 2])
                lines.append({
                    "sku_id": sku["sku_id"],
                    "product_id": sku["product_id"],
                    "name": product["name"],
                    "size": sku["size"],
                    "color": sku["color"],
                    "quantity": quantity,
                    "unit_price_cents": product["price_cents"],
                    "line_total_cents": product["price_cents"] * quantity,
                })
            subtotal = sum(line["line_total_cents"] for line in lines)
            shipping = 0 if subtotal >= 5000 else 499
            t = HISTORY_START + timedelta(minutes=rng.randrange(span_minutes))
            events = []
            for step_status in STATUS_PATHS[status]:
                if events:
                    t += timedelta(hours=rng.randint(2, 30))
                events.append({
                    "status": step_status,
                    "message": EVENT_MESSAGES[step_status],
                    "timestamp": iso(t),
                })
            drafts.append({
                "user_id": user_id,
                "status": status,
                "lines": lines,
                "subtotal_cents": subtotal,
                "shipping_cents": shipping,
                "total_cents": subtotal + shipping,
                "created_at": events[0]["timestamp"],
                "updated_at": events[-1]["timestamp"],
                "events": events,
            })
    # Sort by creation time (ISO strings sort correctly), then number them.
    drafts.sort(key=lambda o: (o["created_at"], o["user_id"]))
    orders = [{"order_id": f"O-{10001 + i}", **draft} for i, draft in enumerate(drafts)]
    payments = []
    for i, order in enumerate(orders):
        payments.append({
            "payment_id": f"PAY-{10001 + i}",
            "order_id": order["order_id"],
            "amount_cents": order["total_cents"],
            "status": "PAYMENT_REFUNDED" if order["status"] == "CANCELLED" else "PAYMENT_SUCCEEDED",
        })
    return orders, payments


def dump(name: str, obj) -> None:
    text = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    with open(OUT_DIR / name, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(f"wrote {OUT_DIR / name}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)

    products = build_products(rng)
    skus = build_skus(products)
    inventory = build_inventory(rng, skus)
    users = [{"user_id": uid, "name": name, "email": email, "city": city}
             for uid, name, email, city in USERS]
    orders, payments = build_orders(rng, products, skus)

    dump("catalog.json", {"products": products, "skus": skus})
    dump("inventory.json", inventory)
    dump("customers.json", {"users": users})
    dump("orders.json", orders)
    dump("payments.json", payments)
    print(f"products={len(products)} skus={len(skus)} orders={len(orders)} payments={len(payments)}")


if __name__ == "__main__":
    main()
