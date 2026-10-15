<?php
require_once dirname(__DIR__).'/lib/classes/shopUnivimportPluginImporter.class.php';
function check($condition, $message) {
    if (!$condition) {
        fwrite(STDERR, 'FAIL: '.$message."\n");
        exit(1);
    }
}
$row = shopUnivimportPluginImporter::row('<offer id="test-1" available="true"><name>Стул</name><vendorCode>0123</vendorCode><price>1599.00</price><currencyId>RUB</currencyId><quantity>4</quantity><picture>https://example.com/a.jpg?x=1&amp;y=2</picture><picture>https://example.com/a.jpg?x=1&amp;y=2</picture><picture>file:///etc/passwd</picture></offer>');
check($row['id']==='test-1', 'offer id');
check($row['sku']==='0123', 'leading zero SKU');
check($row['price']===1599.0, 'price');
check($row['count']===4.0, 'quantity');
check(count($row['images'])===1, 'only one deduplicated HTTP image');
check($row['images'][0]==='https://example.com/a.jpg?x=1&y=2', 'XML escaping decoded');
try {
    shopUnivimportPluginImporter::row('<offer id="a"><name>X</name><currencyId>USD</currencyId><price>2</price></offer>');
    check(false, 'USD must fail without conversion');
} catch (RuntimeException $e) { }
echo "YML row checks: OK\n";
