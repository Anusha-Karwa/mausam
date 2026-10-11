-- Mausam schema (Neon PostgreSQL). Idempotent: safe to run again and again.
-- Every user-owned row carries user_id so the app can grow from one user (Meera) to many.

CREATE TABLE IF NOT EXISTS users (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS settings (
  user_id INT PRIMARY KEY REFERENCES users(id),
  report_email TEXT,
  report_enabled BOOLEAN DEFAULT true,
  daily_tip_enabled BOOLEAN DEFAULT true,
  risk_profile TEXT DEFAULT 'moderate',
  target_allocation JSONB DEFAULT '{"equity":70,"debt":20,"international":10}',
  thresholds JSONB DEFAULT '{
    "equity_week_lag_pp":1.0, "equity_month_lag_pp":2.0,
    "debt_week_lag_pp":0.15, "debt_month_lag_pp":0.4,
    "intl_week_lag_pp":1.5, "intl_month_lag_pp":3.0,
    "consecutive_weeks_to_watch":3, "allocation_drift_pp":10,
    "market_wide_band_pp":0.75, "debt_market_wide_band_pp":0.1, "intl_market_wide_band_pp":1.0
  }',
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS benchmarks (
  key TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  yahoo_ticker TEXT,
  proxy_scheme_code INT,
  currency TEXT DEFAULT 'INR',
  asset_class TEXT
);

CREATE TABLE IF NOT EXISTS funds (
  id SERIAL PRIMARY KEY,
  user_id INT REFERENCES users(id),
  scheme_code INT NOT NULL,
  scheme_name TEXT NOT NULL,
  short_name TEXT,
  fund_house TEXT,
  scheme_category TEXT,
  peer_group TEXT,
  asset_class TEXT NOT NULL,
  benchmark_key TEXT REFERENCES benchmarks(key),
  benchmark_is_override BOOLEAN DEFAULT false,
  sip_amount NUMERIC(12,2),
  sip_day SMALLINT,
  colour TEXT,
  is_active BOOLEAN DEFAULT true,
  added_at TIMESTAMPTZ DEFAULT now(),
  removed_at TIMESTAMPTZ,
  UNIQUE (user_id, scheme_code)
);

CREATE TABLE IF NOT EXISTS nav_history (
  scheme_code INT NOT NULL,
  nav_date DATE NOT NULL,
  nav NUMERIC(14,4) NOT NULL,
  source TEXT DEFAULT 'mfapi',          -- mfapi | amfi (official NAVAll.txt fallback)
  PRIMARY KEY (scheme_code, nav_date)
);

CREATE TABLE IF NOT EXISTS index_history (
  benchmark_key TEXT REFERENCES benchmarks(key),
  trade_date DATE NOT NULL,
  close NUMERIC(14,4) NOT NULL,
  source TEXT,
  PRIMARY KEY (benchmark_key, trade_date)
);

CREATE TABLE IF NOT EXISTS category_peers (
  scheme_category TEXT NOT NULL,
  scheme_code INT NOT NULL,
  scheme_name TEXT,
  PRIMARY KEY (scheme_category, scheme_code)
);

CREATE TABLE IF NOT EXISTS category_stats (
  scheme_category TEXT NOT NULL,
  as_of DATE NOT NULL,
  ret_1w NUMERIC(8,4), ret_1m NUMERIC(8,4), ret_ytd NUMERIC(8,4),
  ret_1y NUMERIC(8,4), ret_3y_cagr NUMERIC(8,4),
  peer_count INT,
  PRIMARY KEY (scheme_category, as_of)
);

CREATE TABLE IF NOT EXISTS transactions (
  id SERIAL PRIMARY KEY,
  user_id INT REFERENCES users(id),
  fund_id INT REFERENCES funds(id),
  txn_type TEXT NOT NULL CHECK (txn_type IN ('sip','buy','sell')),
  txn_date DATE NOT NULL,
  amount NUMERIC(14,2) NOT NULL,
  nav NUMERIC(14,4) NOT NULL,
  units NUMERIC(16,4) NOT NULL,
  is_simulated BOOLEAN DEFAULT false,
  note TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS weekly_snapshots (
  id SERIAL PRIMARY KEY,
  user_id INT REFERENCES users(id),
  week_ending DATE NOT NULL,
  fund_id INT REFERENCES funds(id),
  scheme_code INT,
  nav NUMERIC(14,4), nav_date DATE,
  ret_1w NUMERIC(8,4), ret_1m NUMERIC(8,4), ret_ytd NUMERIC(8,4), ret_1y NUMERIC(8,4), ret_3y_cagr NUMERIC(8,4),
  bench_ret_1w NUMERIC(8,4), bench_ret_1m NUMERIC(8,4), bench_ret_ytd NUMERIC(8,4),
  cat_ret_1w NUMERIC(8,4), cat_ret_1m NUMERIC(8,4), cat_ret_ytd NUMERIC(8,4),
  delta_bench_1w NUMERIC(8,4), delta_cat_1w NUMERIC(8,4),
  attribution TEXT,
  units NUMERIC(16,4), invested NUMERIC(14,2), value NUMERIC(14,2),
  flags JSONB,
  UNIQUE (user_id, week_ending, fund_id)
);

CREATE TABLE IF NOT EXISTS market_context (
  week_ending DATE PRIMARY KEY,
  index_moves JSONB,
  macro_summary TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS news_items (
  id SERIAL PRIMARY KEY,
  week_ending DATE,
  published_at TIMESTAMPTZ,
  title TEXT NOT NULL,
  source TEXT,
  url TEXT,
  why_it_matters TEXT,
  tags TEXT[],
  UNIQUE (url)
);

CREATE TABLE IF NOT EXISTS reports (
  id SERIAL PRIMARY KEY,
  user_id INT REFERENCES users(id),
  week_ending DATE NOT NULL,
  verdict TEXT NOT NULL CHECK (verdict IN ('No action needed','One fund worth watching','Consider reviewing your allocation')),
  verdict_reason TEXT NOT NULL,
  facts_json JSONB NOT NULL,
  report_json JSONB NOT NULL,
  report_html TEXT,
  model TEXT,
  used_fallback BOOLEAN DEFAULT false,
  generated_at TIMESTAMPTZ DEFAULT now(),
  emailed_at TIMESTAMPTZ,
  trigger TEXT,
  UNIQUE (user_id, week_ending)
);

CREATE TABLE IF NOT EXISTS forecasts (
  scheme_code INT NOT NULL,
  run_date DATE NOT NULL,
  horizon_days INT NOT NULL,
  target_date DATE,
  point NUMERIC(14,4),
  lo80 NUMERIC(14,4), hi80 NUMERIC(14,4),
  lo95 NUMERIC(14,4), hi95 NUMERIC(14,4),
  path JSONB,
  model TEXT,
  arima_order TEXT,
  hybrid_weight_arima NUMERIC(5,3),
  explanation TEXT,
  PRIMARY KEY (scheme_code, run_date, horizon_days)
);

CREATE TABLE IF NOT EXISTS forecast_metrics (
  scheme_code INT NOT NULL,
  run_date DATE NOT NULL,
  model TEXT NOT NULL,
  horizon_days INT NOT NULL,
  mae NUMERIC(12,6), mape NUMERIC(10,4), rmse NUMERIC(12,6),
  directional_accuracy NUMERIC(6,3),
  skill_vs_naive NUMERIC(8,4),
  coverage80 NUMERIC(6,3),
  n_origins INT,
  details JSONB,
  PRIMARY KEY (scheme_code, run_date, model, horizon_days)
);

CREATE TABLE IF NOT EXISTS alerts (
  id SERIAL PRIMARY KEY,
  user_id INT REFERENCES users(id),
  fund_id INT REFERENCES funds(id),
  kind TEXT,
  severity TEXT,
  message TEXT,
  link TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  read_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS daily_tips (
  tip_date DATE PRIMARY KEY,
  tip TEXT NOT NULL,
  facts_json JSONB
);

CREATE TABLE IF NOT EXISTS chat_messages (
  id SERIAL PRIMARY KEY,
  user_id INT REFERENCES users(id),
  role TEXT CHECK (role IN ('user','assistant')),
  content TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS job_runs (
  id SERIAL PRIMARY KEY,
  job TEXT NOT NULL,
  started_at TIMESTAMPTZ DEFAULT now(),
  finished_at TIMESTAMPTZ,
  status TEXT,
  details JSONB
);

-- Columns added after the first version (kept idempotent for existing databases)
ALTER TABLE funds ADD COLUMN IF NOT EXISTS peer_group TEXT;
ALTER TABLE nav_history ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'mfapi';
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS link TEXT;
ALTER TABLE forecast_metrics ADD COLUMN IF NOT EXISTS details JSONB;

CREATE INDEX IF NOT EXISTS idx_nav_scheme_date ON nav_history (scheme_code, nav_date DESC);
CREATE INDEX IF NOT EXISTS idx_index_key_date ON index_history (benchmark_key, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_txn_user_fund_date ON transactions (user_id, fund_id, txn_date);
CREATE INDEX IF NOT EXISTS idx_snap_user_week ON weekly_snapshots (user_id, week_ending);
CREATE INDEX IF NOT EXISTS idx_news_week ON news_items (week_ending, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_user ON alerts (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_job_started ON job_runs (job, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_user ON chat_messages (user_id, created_at DESC);
