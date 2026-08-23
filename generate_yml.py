import requests
import xml.etree.ElementTree as ET
from xml.dom import minidom

API = "https://norden.group/api-products/"
SECRET = "${NORDEN_SECRET}"


def get_products():
    headers = {"secret": SECRET}
    page = 1
    products = []
    while True:
        r = requests.get(API, headers=headers, params={"page": page}, timeout=60)
        r.raise_for_status()
        data = r.json()
        products.extend(data.get("products", []))
        page_data = data.get("page_data", {})
        if page >= int(page_data.get("pages", page)) and len(data.get("products", [])) < 500:
            break
        if len(data.get("products", [])) == 0:
            break
        page += 1
    return products


def make_yml(products):
    root = ET.Element("yml_catalog", date="2026-08-23")
    shop = ET.SubElement(root, "shop")
    ET.SubElement(shop, "name").text = "Norden"
    ET.SubElement(shop, "company").text = "Norden"

    offers = ET.SubElement(shop, "offers")

    for p in products:
        qty = int(float(p.get("qty", 0)))
        offer = ET.SubElement(offers, "offer", id=str(p.get("product_code", "")), available=str(qty > 0).lower())
        ET.SubElement(offer, "name").text = p.get("name", "")
        ET.SubElement(offer, "vendorCode").text = p.get("product_code", "")
        ET.SubElement(offer, "vendor").text = "Norden"
        ET.SubElement(offer, "price").text = str(p.get("price", "0"))
        ET.SubElement(offer, "oldprice").text = str(p.get("price_rrc", ""))
        ET.SubElement(offer, "description").text = p.get("description", "")

        for img in p.get("images", []):
            ET.SubElement(offer, "picture").text = img

        for feature in p.get("features", []):
            ET.SubElement(offer, "param", name=feature.get("name", "")).text = feature.get("value", "")

    xml = minidom.parseString(ET.tostring(root, encoding="utf-8")).toprettyxml(indent="  ")
    with open("norden.yml", "w", encoding="utf-8") as f:
        f.write(xml)


if __name__ == "__main__":
    make_yml(get_products())
