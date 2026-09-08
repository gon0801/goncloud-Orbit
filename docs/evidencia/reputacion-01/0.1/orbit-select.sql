-- 0.1 Orbit (docker exec orbit-db-1 psql -U orbit -d orbit)
SELECT platform, count(*), count(DISTINCT product_id) FROM listing GROUP BY 1 ORDER BY 1;
SELECT platform, external_id, seller_sku FROM listing ORDER BY platform, external_id LIMIT 12;
SELECT count(*) AS listings_sin_odoo FROM listing l LEFT JOIN product p ON p.id=l.product_id WHERE p.odoo_sku IS NULL;
-- volcado cruce (psql -A -t):
SELECT platform, external_id, seller_sku FROM listing ORDER BY 1;
SELECT odoo_sku FROM product;

-- 0.1 Bridge (sqlite3 "file:.../bridge.db?mode=ro")
-- .tables + .schema sku_mapping / amazon_sku_mapping / meli_sku_mapping / meli_listings_cache
SELECT seller_sku, odoo_default_code, asin FROM amazon_sku_mapping;
SELECT channel, sku, remote_item_id, site FROM sku_mapping;
SELECT status, count(*), min(updated_at), max(updated_at) FROM meli_listings_cache GROUP BY 1;
SELECT channel, site, count(*) FROM sku_mapping GROUP BY 1,2;
