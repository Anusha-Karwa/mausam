-- Reference data: the default user, settings and benchmark indices.
-- Tickers were verified on 11 Oct 2026 with scripts/verify_tickers.py (see docs/DATA_SOURCES.md).
-- Proxies are direct-growth index funds on mfapi.in that track the same index; used when Yahoo fails.

INSERT INTO users (id, name, email) VALUES (1, 'Meera', NULL) ON CONFLICT (id) DO NOTHING;
SELECT setval(pg_get_serial_sequence('users', 'id'), GREATEST((SELECT MAX(id) FROM users), 1));

INSERT INTO settings (user_id) VALUES (1) ON CONFLICT (user_id) DO NOTHING;

INSERT INTO benchmarks (key, name, yahoo_ticker, proxy_scheme_code, currency, asset_class) VALUES
  ('NIFTY50',       'Nifty 50',              '^NSEI',               119063, 'INR', 'equity'),
  ('MIDCAP150',     'Nifty Midcap 150',      'NIFTYMIDCAP150.NS',   147622, 'INR', 'equity'),
  ('SMALLCAP100',   'Nifty Smallcap 100',    '^CNXSC',              NULL,   'INR', 'equity'),
  ('SMALLCAP250',   'Nifty Smallcap 250',    'NIFTYSMLCAP250.NS',   147623, 'INR', 'equity'),
  ('SP500',         'S&P 500',               '^GSPC',               148381, 'USD', 'international'),
  ('NASDAQ100',     'Nasdaq 100',            '^NDX',                149219, 'USD', 'international'),
  ('USDINR',        'US dollar in rupees',   'USDINR=X',            NULL,   'INR', 'fx'),
  ('CATEGORY_ONLY', 'Category average only', NULL,                  NULL,   'INR', 'debt')
ON CONFLICT (key) DO UPDATE SET
  name = EXCLUDED.name,
  yahoo_ticker = EXCLUDED.yahoo_ticker,
  proxy_scheme_code = EXCLUDED.proxy_scheme_code,
  currency = EXCLUDED.currency,
  asset_class = EXCLUDED.asset_class;
