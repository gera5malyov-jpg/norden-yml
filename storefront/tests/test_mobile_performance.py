import base64
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from stage53_mobile_performance import externalize_fonts, patch_font_links

class FontDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.blobs=[b'wOFF'+bytes([i])*1200 for i in range(18)]
        self.css=''.join('@font-face{font-family:Montserrat;font-weight:'+str((i%9+1)*100)+';src:url(data:application/x-font-woff;base64,'+base64.b64encode(b).decode()+") format('woff');}" for i,b in enumerate(self.blobs))

    def test_external_fonts_preserve_every_original_binary(self):
        css,files=externalize_fonts(self.css)
        self.assertNotIn('base64,',css)
        self.assertEqual(list(files.values()),self.blobs)
        self.assertEqual(css.count('font-display:swap;'),18)
        self.assertLess(len(css),len(self.css)//4)

    def test_changed_or_invalid_font_source_aborts(self):
        with self.assertRaises(ValueError):externalize_fonts(self.css.replace('@font-face','@invalid',1))
        with self.assertRaises(ValueError):externalize_fonts(self.css.replace('d09GR', '!!!',1))

    def test_font_link_preserves_original_for_other_domains(self):
        old='<link href="{$wa_real_theme_url}css/fonts/{$theme_settings.main__font}.min.css?v{$wa_theme_version}" rel="stylesheet" />'
        new=patch_font_links(old+'\n'+old)
        self.assertEqual(new.count(old),2)
        self.assertEqual(new.count('montserrat.megapolis53.css'),2)
        self.assertIn("$wa->domainUrl() == 'https://profikompany.ru'",new)
        with self.assertRaises(ValueError):patch_font_links(old)

if __name__=='__main__':unittest.main()
