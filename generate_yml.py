import os
import requests
import xml.etree.ElementTree as ET
from xml.dom import minidom

API = "https://norden.group/api-products/"
CAT_API = "https://norden.group/api-categories/"
SECRET = os.environ.get("NORDEN_SECRET", "")


def headers():
    return {"secret": SECRET}


def get_categories():
    r = requests.get(CAT_API, headers=headers(), timeout=60)
    r.raise_for_status()
    return r.json()


def get_chair_categories():
    categories = get_categories()
    result = set()
    for cat in categories:
        path = str(cat.get("id_path", ""))
        if path == "36" or path.startswith("36/"):
            result.add(str(cat.get("category_id")))
    return result


def get_products():
    page = 1
    products = []
    while True:
        r = requests.get(API, headers=headers(), params={"page": page}, timeout=60)
        r.raise_for_status()
        data = r.json()
        batch = data.get("products", [])
        products.extend(batch)
        if len(batch) < 500:
            break
        page += 1
    return products


def filter_products(products):
    allowed = get_chair_categories()
    result = []
    for p in products:
        cats = set(str(p.get("category", "")).split(","))
        if cats.intersection(allowed):
            result.append(p)
    return result


def make_description(p):
    if p.get("description"):
        return p.get("description")
    name = p.get("name", "Кресло или стул Norden")
    return (
        f"{name}. Эргономичное кресло для дома, офиса и рабочих помещений. "
        "Продуманная конструкция обеспечивает комфорт при ежедневном использовании. "
        "Качественные материалы, надежный каркас и удобная посадка делают модель практичным решением для рабочего места."
    )


def make_yml(products):
    root = ET.Element("yml_catalog", date="2026-08-23")
    shop = ET.SubElement(root, "shop")
    ET.SubElement(shop, "name").text = "Norden"
    ET.SubElement(shop, "company").text = "Norden"
    offers = ET.SubElement(shop, "offers")

    for p in products:
        qty = int(float(p.get("qty", 0)))
        offer = ET.SubElement(
            offers,
            "offer",
            id=str(p.get("product_code", "")),
            available=str(qty > 0).lower()
        )

        ET.SubElement(offer, "name").text = p.get("name", "")
        ET.SubElement(offer, "vendorCode").text = p.get("product_code", "")
        ET.SubElement(offer, "vendor").text = "Norden"
        ET.SubElement(offer, "price").text = str(p.get("price", "0"))
        ET.SubElement(offer, "oldprice").text = str(p.get("price_rrc", ""))
        ET.SubElement(offer, "quantity").text = str(qty)
        ET.SubElement(offer, "stock_quantity").text = str(qty)
        ET.SubElement(offer, "description").text = make_description(p)

        for img in p.get("images", []):
            ET.SubElement(offer, "picture").text = img

        for feature in p.get("features", []):
            ET.SubElement(offer, "param", name=str(feature.get("name", ""))).text = str(feature.get("value", ""))

    xml = minidom.parseString(ET.tostring(root, encoding="utf-8")).toprettyxml(indent="  ")
    with open("norden.yml", "w", encoding="utf-8") as f:
        f.write(xml)


if __name__ == "__main__":
    make_yml(filter_products(get_products()))
