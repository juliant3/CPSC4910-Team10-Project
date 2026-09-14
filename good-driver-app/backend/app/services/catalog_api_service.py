"""
Wraps whichever public product API you pick (eBay Browse API, Etsy Open
API, Overstock, etc). Keeping this isolated means swapping providers
later only touches this one file.

- search_products(sponsor, query, category=None) -> list of raw external
    product dicts (name, price_usd, availability, image, description)
- sync_sponsor_catalog(sponsor) -> pulls sponsor's chosen products,
    converts price_usd -> price_points using sponsor.point_value_usd,
    upserts into the local Product table, updates last_synced_at
- get_live_price_and_availability(product) -> called at "add to cart"
    time per spec (must reflect live price/availability, not the cache)
- convert_usd_to_points(price_usd, sponsor) -> price_usd / sponsor.point_value_usd
"""
