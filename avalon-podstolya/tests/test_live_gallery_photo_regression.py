"""Protect the image markup used by Avalon cafe table product pages."""
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import parser


def test_cafe_table_main_gallery_uses_current_image_path():
    # The main gallery supplies a Bitrix resize_cache URL; the YML must
    # contain the original full-resolution upload/iblock image.
    src = ("https://www.avalonmebel.ru/upload/resize_cache/iblock/5df/"
           "1000_530_1/5dfb8d4cc4ae92edb5af75349bcae565.jpg")
    html = ('<div class="magnific-gallery"><div><a href="' + src + '">'
            '<img class="magnific-gallery__main" src="' + src + '">'
            '</a></div></div>')
    photos = parser.pictures(BeautifulSoup(html, "html.parser"))
    assert photos == [
        "https://www.avalonmebel.ru/upload/iblock/5df/"
        "5dfb8d4cc4ae92edb5af75349bcae565.jpg"
    ]


def test_dimensions_photo_remains_last():
    main = "/upload/resize_cache/iblock/5df/1000_530_1/5dfb8d4cc4ae92edb5af75349bcae565.jpg"
    drawing = "/upload/iblock/abc/dimensions.jpg"
    html = ('<div class="magnific-gallery"><div><img class="magnific-gallery__main" src="' + main + '"></div></div>'
            '<div class="sku__chars"><div class="magnific-gallery__item1"><a href="' + drawing + '"></a></div></div>')
    photos = parser.pictures(BeautifulSoup(html, "html.parser"))
    assert len(photos) == 2
    assert photos[0].endswith("/5dfb8d4cc4ae92edb5af75349bcae565.jpg")
    assert photos[-1].endswith("/dimensions.jpg")
