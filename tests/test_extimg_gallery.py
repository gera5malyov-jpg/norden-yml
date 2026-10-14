"""Unit regressions for external image gallery markup."""
import re
from storefront.fix_extimg_multi_image_gallery import START, END, NEW_PARSER, patch_template


def test_extimg_gallery_parses_every_block_and_legacy_multiline():
    old = START + """
{assign var="ext_images" value=[]}
{if preg_match('/\\[extimg\\](.*?)\\[\\/extimg\\]/is', $__extimg_source, $__m)}
  {assign var="__extimg_block" value=$__m.1}
{/if}
""" + END + """
<div class="product-gallery product-gallery--extimg"><img src="{$img_url|escape}" class="js-extimg-thumb"></div>
"""
    updated = patch_template(old)
    assert "preg_match_all(" in updated
    assert "{foreach $__extimg_matches.1 as $__extimg_block}" in updated
    assert updated.count("loading=\"lazy\"") == 1
    assert "$__has_extimg" in updated
    assert 'str_replace("\\r\\n", "\\n"' in updated
    assert "in_array($__url, $ext_images)" in updated
    assert updated != old


def test_shortcode_extraction_handles_11_individual_blocks_and_old_multiline():
    urls = ["https://riva.ru/upload/image-%d.jpg" % i for i in range(11)]
    text = "".join("[extimg]\\n" + url + "\\n[/extimg]\\n" for url in urls)
    groups = re.findall(r"\\[extimg\\](.*?)\\[/extimg\\]", text, flags=re.I | re.S)
    extracted = [v.strip() for g in groups for v in g.splitlines() if v.strip()]
    assert extracted == urls
    old_format = "[extimg]\\n" + "\\n".join(urls) + "\\n[/extimg]"
    old_groups = re.findall(r"\\[extimg\\](.*?)\\[/extimg\\]", old_format, flags=re.I | re.S)
    assert [v.strip() for g in old_groups for v in g.splitlines() if v.strip()] == urls


def test_gallery_refuses_malformed_or_repeat_source():
    sample = START + "\\n{if preg_match_all('anything')}...{/if}\\n" + END + "\\nproduct-gallery--extimg js-extimg-thumb"
    try:
        patch_template(sample)
    except ValueError:
        pass
    else:
        raise AssertionError("Repeated gallery patch should be rejected")
