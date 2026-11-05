-- PEZ 01 P2: identidad MeLi explicita hacia el producto canonico de Odoo.
-- Expansiva, no re-runnable. Reversa: 0075_reversa_meli_sku_alias.sql.
BEGIN;

CREATE TABLE meli_sku_alias (
    seller_sku TEXT PRIMARY KEY,
    product_id BIGINT NOT NULL REFERENCES product(id),
    evidencia TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT meli_sku_alias_sku_limpio
        CHECK (seller_sku = btrim(seller_sku) AND seller_sku <> ''),
    CONSTRAINT meli_sku_alias_evidencia_no_vacia CHECK (btrim(evidencia) <> '')
);

CREATE INDEX ON meli_sku_alias (product_id);

COMMENT ON TABLE meli_sku_alias IS
  'Correspondencia MeLi verificada por el administrador. Conserva SELLER_SKU; '
  'el producto y su costo siguen siendo los canonicos de Odoo/accounting. '
  'El catalogo rechaza alias contradictorio con un SKU canonico antes de escribir.';

REVOKE ALL ON meli_sku_alias FROM PUBLIC, app_ingest, app_read, app_decide, app_admin;
GRANT SELECT ON meli_sku_alias TO app_ingest, app_read, app_decide, app_admin;
GRANT INSERT, UPDATE, DELETE ON meli_sku_alias TO app_admin;

-- El lote puede corregir la correspondencia, nunca el costo ni la identidad MeLi.
GRANT UPDATE (product_id) ON listing_miembro TO app_admin;

CREATE FUNCTION public.meli_sku_alias_bloquear() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    LOCK TABLE public.product, public.sku_cost, public.listing IN SHARE MODE;
    LOCK TABLE public.meli_sku_alias, public.listing_miembro IN SHARE ROW EXCLUSIVE MODE;
END;
$$;

COMMENT ON FUNCTION public.meli_sku_alias_bloquear() IS
  'PEZ 01: el administrador bloquea el conjunto estable antes de comprobar y '
  'corregir sus correspondencias. SECURITY DEFINER permite solo estos locks '
  'fijos, sin argumentos ni DML, para no conceder UPDATE sobre product, '
  'sku_cost ni listing. Los locks duran hasta cerrar la transaccion llamadora.';

REVOKE EXECUTE ON FUNCTION public.meli_sku_alias_bloquear()
    FROM PUBLIC, app_ingest, app_read, app_decide;
GRANT EXECUTE ON FUNCTION public.meli_sku_alias_bloquear() TO app_admin;

COMMIT;
