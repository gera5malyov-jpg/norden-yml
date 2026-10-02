import csv, io, json
from pathlib import Path
from xml.etree import ElementTree as ET

def load_csv(data: bytes, delimiter=None):
    text=data.decode("utf-8-sig")
    sample=text[:4096]
    dialect=csv.Sniffer().sniff(sample, delimiters=delimiter or ",;\t")
    return list(csv.DictReader(io.StringIO(text), dialect=dialect))

def load_xml_yml(data: bytes):
    root=ET.fromstring(data)
    rows=[]
    for offer in root.findall(".//offer"):
        row=dict(offer.attrib)
        for child in offer:
            if child.tag=="param":
                key=child.attrib.get("name","").strip()
                if key: row[key]=(child.text or "").strip()
            elif child.tag=="picture":
                row.setdefault("pictures",[]).append((child.text or "").strip())
            else:
                row[child.tag]=(child.text or "").strip()
        rows.append(row)
    return rows

def load_xlsx(data: bytes, sheet=None):
    from openpyxl import load_workbook
    wb=load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws=wb[sheet] if sheet else wb[wb.sheetnames[0]]
    rows=ws.iter_rows(values_only=True)
    headers=[str(x).strip() if x is not None else "" for x in next(rows)]
    return [dict(zip(headers,row)) for row in rows if any(v is not None for v in row)]

def load_pdf(data: bytes):
    raise RuntimeError("PDF adapter requires supplier-specific extraction rules and mandatory review; automatic generic import is intentionally blocked.")
