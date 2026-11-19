"""Shop vocabulary shared by the rule-based planner: categories (and what shoppers call them), colours, sizes, brands.

The category slugs, colour palette, sizes and brands are the catalog's own (see data/seed/v2/catalog.json); the synonym
lists are what people actually type. Keeping it as data keeps the planner readable.
"""
CATEGORY_WORDS: dict[str, list[str]] = {
    "headphones": ["headphone", "headphones", "earbud", "earbuds", "earphone", "earphones", "cans", "headset", "airpods"],
    "hoodies": ["hoodie", "hoodies", "hoody", "sweatshirt", "sweatshirts", "pullover"],
    "backpacks": ["backpack", "backpacks", "rucksack", "rucksacks", "knapsack", "bookbag"],
    "watches": ["watch", "watches", "timepiece", "wristwatch"],
    "sunglasses": ["sunglasses", "sunglass", "sunnies", "shades"],
    "t-shirts": ["tshirt", "tshirts", "tee", "tees", "shirt", "shirts"],
    "jeans": ["jeans", "jean", "denim"],
    "jackets": ["jacket", "jackets", "coat", "coats", "parka", "parkas", "windbreaker", "anorak"],
    "shoes": ["shoe", "shoes", "sneaker", "sneakers", "trainer", "trainers", "kicks", "boot", "boots", "loafers", "footwear"],
    "accessories": ["accessory", "accessories"],
}
WORD_TO_CATEGORY = {w: slug for slug, words in CATEGORY_WORDS.items() for w in words}

COLORS = ["beige", "black", "blue", "brown", "burgundy", "green", "grey", "navy", "olive", "orange", "pink", "red", "white", "yellow"]
COLOR_ALIASES = {"gray": "grey", "navi": "navy", "whit": "white", "blak": "black", "oilive": "olive", "burgandy": "burgundy",
                 "maroon": "burgundy", "tan": "beige", "cream": "beige"}

SIZES = {"XS", "S", "M", "L", "XL", "XXL", "ONE", "6", "7", "8", "9", "10", "11", "12", "28", "30", "32", "34", "36"}
LETTER_SIZES = {"xs": "XS", "s": "S", "m": "M", "l": "L", "xl": "XL", "xxl": "XXL"}

BRANDS = ["Brevlan", "Corvane", "Dunmoor", "Ellistra", "Fenrowe", "Gallowmere", "Hartwick Lane", "Ivorside", "Jastrel", "Kelvane",
          "Lorrimere", "Mirelle Ash", "Nordhaven", "Orlinde", "Ostrava Keel", "Pellworth", "Quinnell", "Rhydwen", "Strathvale",
          "Tessaly", "Umbrarin", "Vantesk", "Wrenfield", "Yarrowby", "Zephyrine"]

# Words that describe a product (they go into the free-text search), and product nouns that are not categories.
FEATURES = ["waterproof", "leather", "laptop", "wireless", "bluetooth", "polarised", "polarized", "cropped", "slim", "oversized",
            "foldable", "strap", "rfid", "hiking", "running", "gym", "travel", "canvas", "cotton", "wool", "fleece", "insulated",
            "breathable", "lightweight", "formal", "casual", "stainless", "zip"]
NOUNS = ["belt", "wallet", "scarf", "hat", "cap", "gloves", "beanie", "socks", "tie"]

# Not shopping: if one of these is in the question, a category word in it does not make it a product search.
OUT_OF_SCOPE = (r"weather|poem|poetry|translate|homework|capital of|who won|football|soccer|wifi|wi-fi|bitcoin|crypto|\bjoke\b|"
                r"recipe|lyrics|\bnews\b|stock market|write (me|a)\b|\bessay\b|president|\bcode\b")
STOP = {"show", "shows", "shop", "some", "with", "them", "that", "this", "what", "have", "like", "want", "need", "looking", "please",
        "plain", "which", "there", "where", "your", "from", "about", "just", "also", "size", "sizes", "stuff", "pair", "bucks",
        "dollars", "under", "over", "below", "above", "less", "more", "than", "between", "cheap", "best", "good", "nice", "fire",
        "ones", "something", "anything", "colour", "color", "price", "cost", "does", "come", "stock", "left", "item", "items"}
