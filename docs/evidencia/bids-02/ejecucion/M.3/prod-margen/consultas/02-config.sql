SELECT settings->'ads_target_acos_pct_amazon_mx' AS mx, settings->'ads_target_acos_pct_amazon_us' AS us, settings->>'ads_optimizer_mode' AS mode FROM config_version ORDER BY id DESC LIMIT 1;
