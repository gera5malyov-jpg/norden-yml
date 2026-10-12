"""Domain-scoped mobile home and font delivery, with fail-closed anchors."""
import base64
import re

SITE='wa-data/public/site/themes/pureMegapolis42/'
SHOP='wa-data/public/shop/themes/pureMegapolis42/'
FONT_LINK='<link href="{$wa_real_theme_url}css/fonts/{$theme_settings.main__font}.min.css?v{$wa_theme_version}" rel="stylesheet" />'

def externalize_fonts(css):
    pattern=r'url\(data:application/x-font-woff;base64,([A-Za-z0-9+/=]+)\)'
    blobs=re.findall(pattern,css)
    if len(blobs)!=18 or css.count('@font-face')!=18:
        raise ValueError('Unexpected original font faces')
    files={}
    for i,encoded in enumerate(blobs,1):
        binary=base64.b64decode(encoded,validate=True)
        if not binary.startswith(b'wOFF') or not 1000<len(binary)<100000:
            raise ValueError('Unexpected original WOFF binary')
        name='montserrat-megapolis53-%02d.woff'%i
        files[name]=binary
        css=css.replace('url(data:application/x-font-woff;base64,'+encoded+')',"url('"+name+"')",1)
    if 'base64,' in css or len(css)>16000:raise ValueError('Inline fonts remain')
    return css.replace('@font-face{','@font-face{font-display:swap;'),files

def patch_font_links(source):
    if source.count(FONT_LINK)!=2 or 'montserrat.megapolis53.css' in source:
        raise ValueError('Audited font links changed')
    new='''{if $wa->domainUrl() == 'https://profikompany.ru' && $theme_settings.main__font == "montserrat"}
      <link href="{$wa_real_theme_url}css/fonts/montserrat.megapolis53.css?v=20261012" rel="stylesheet" />
      {else}'''+FONT_LINK+'''{/if}'''
    return source.replace(FONT_LINK,new)

def once(source,old,new):
    if source.count(old)!=1:raise ValueError('Audited anchor changed: '+old[:65])
    return source.replace(old,new,1)

def patch_home(source):
    old='{include file="`$wa_parent_theme_path`/layouts/layout.slider.html" inline}'
    mobile='''{if $wa->domainUrl() == 'https://profikompany.ru'}
  <section class="megapolis-mobile-home container" aria-label="Выбор мебели">
    <form class="megapolis-mobile-search" role="search" method="get" action="{$wa->getUrl("shop/frontend/search")}">
      <label class="megapolis-sr-only" for="megapolis-home-search">Поиск товаров</label>
      <input id="megapolis-home-search" type="search" name="query" placeholder="Название, модель или артикул" autocomplete="off" value="{$smarty.get.query|default:''|escape}">
      <button type="submit" aria-label="Найти товар">Найти</button>
    </form>
    <div class="megapolis-mobile-hero">
      <p class="megapolis-mobile-hero__title">Мебель для дома и офиса</p>
      <p>Выберите модель и оформите заказ онлайн.</p>
      <a href="#megapolis-mobile-categories" class="megapolis-mobile-hero__button">Выбрать мебель</a>
      <small>Доставка из разных городов. Стоимость рассчитаем отдельно.</small>
    </div>
    <nav id="megapolis-mobile-categories" class="megapolis-mobile-categories" aria-label="Категории мебели">
      <h2>Выберите категорию</h2>
      <div class="megapolis-mobile-categories__grid">
        <a href="/category/stul/stulya/">Обеденные стулья</a>
        <a href="/category/stoly_1/stoly/">Обеденные столы</a>
        <a href="/category/stoly_1/stoly-zhurnalnye/">Журнальные столы</a>
        <a href="/category/kompyuternye-kresla/stulya-posetiteley/">Стулья для офиса</a>
        <a href="/category/shkafy/stellazhi/">Стеллажи</a>
        <a href="/category/shkafy/shkafy-raspashnye/">Шкафы</a>
        <a href="/category/stoly_1/stoly-pismennye/">Письменные столы</a>
        <a href="/category/myagkaya-mebel/divany/">Диваны</a>
      </div>
      <details class="megapolis-all-categories">
        <summary>Все категории каталога</summary>
        <div>{foreach $wa->shop->categories(0,0,true,true) as $_megapolis_cat}<a href="{$_megapolis_cat.url|escape}">{$_megapolis_cat.name|escape}</a>{/foreach}</div>
      </details>
    </nav>
  </section>
  <div class="megapolis-original-home-slider">'''+old+'''</div>
{else}'''+old+'''{/if}'''
    source=once(source,old,mobile)
    source=once(source,'{* CATEGORIES WITH IMAGES *}','{* CATEGORIES WITH IMAGES *}\n{if $wa->domainUrl() == \'https://profikompany.ru\'}<div class="megapolis-original-home-categories">{/if}')
    return once(source,'{* PRODUCT SETS *}',"{if $wa->domainUrl() == 'https://profikompany.ru'}</div>{/if}\n\n{* PRODUCT SETS *}")

def patch_slider(source):
    start=source.index('                    <picture class="slider__picture">',source.index('{* megapolis-webp-slider-2026')-300)
    end=source.index('                    </picture>',start)+len('                    </picture>')
    old=source[start:end]
    if old.count('megapolis-webp-slider-2026')!=1:raise ValueError('Slider source changed')
    new='''{if $wa->domainUrl() == 'https://profikompany.ru'}
                    {if !empty($_links[$_image@index])}<a href="{$_links[$_image@index]}">{/if}
                    <picture class="slider__picture">
                      <source media="(max-width: 767px)" srcset="{$wa_parent_theme_url}img/w3c.png">
                      <img class="slider__image" src="{$wa_parent_theme_url}{$_image}"{if strpos($_image, 'img/slider/slider_0') === 0} srcset="{$wa_parent_theme_url}{$_image|replace:'.png':'.webp'} 1x"{/if} decoding="async" loading="{if $_image@index == 0}eager{else}lazy{/if}" fetchpriority="{if $_image@index == 0}high{else}low{/if}"{if !empty($_titles[$_image@index])} alt="{$_titles[$_image@index]|escape}"{/if}>
                    </picture>
                    {if !empty($_links[$_image@index])}</a>{/if}
                  {else}'''+old+'''{/if}'''
    return once(source,old,new)

def patch_index(source):
    source=patch_font_links(source)
    css='<link href="{$wa_real_theme_url}css/user.css?v{$wa_theme_version}" rel="stylesheet" />'
    return once(source,css,css+'''\n    {if $wa->domainUrl() == 'https://profikompany.ru'}
      <link href="{$wa_real_theme_url}css/megapolis-mobile-home.css?v=20261012b" rel="stylesheet" />
      <script src="{$wa_real_theme_url}js/megapolis-mobile-home.js?v=20261012b" defer></script>
    {/if}''')
