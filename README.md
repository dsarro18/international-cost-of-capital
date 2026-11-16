# International Cost of Capital App

A Streamlit app that compares a project's **cost of equity, cost of debt and WACC in USD and in the currency of any selected country**.

**Defaults when the app opens:** Software (System & Application), United States as the selected country, U.S. tax rate, 6.5% equity risk premium, 30% debt financing.

## Data sources (fetched live, with a refresh button)

| Input | Source |
|---|---|
| Industry unlevered betas (U.S.) | Aswath Damodaran, [betas.xls](https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls), column "Unlevered beta" |
| BBB USD borrowing spread | Damodaran, [ratings.html](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html), Baa2/BBB row for large non-financial firms |
| Corporate tax rates | OECD Corporate Tax Statistics, combined statutory CIT rate, latest year. The OECD report page blocks scripts, so the app reads the same table from the OECD data API (`DSD_TAX_CIT@DF_CIT`). |
| 10-year government bond yields | [Trading Economics](https://tradingeconomics.com/bonds). The app tries the `/bonds` table first, then each country's government-bond-yield page. |

The data is cached for 24 hours (bond yields for 6 hours). **🔄 Refresh data fetch** clears the cache and fetches everything again. The *Sources and dates* table shows each source's link and update date. It also marks any value you've overridden by hand.

## Features
- Select an industry and a target country. Adjust the equity risk premium and the target debt share.
- Manual overrides for the unlevered beta, both corporate tax rates, the USD BBB spread and both 10Y yields.
- Optional override for the foreign-currency BBB spread. When the override is off, the app shows the automatically calculated spread.
- A toggle for the project tax rate (U.S. or foreign), applied to both currencies.
- USD and foreign-currency results side by side, an all-industry comparison table, and CSV/Excel downloads.
- Built-in verification checks (see below).

## Method
1. Relever the beta: β_L = β_U × [1 + (1 − t) × D/E], where D/E = d / (1 − d)
2. USD cost of equity: r_E$ = r_f$ + β_L × ERP
3. USD cost of debt: r_D$ = r_f$ + BBB spread
4. Foreign currency, exact (default): 1 + r_FC = (1 + r$) × (1 + r_f,FC) / (1 + r_f$). An additive approximation, r$ + (r_f,FC − r_f$), is available as a toggle.
   The foreign cost of debt is converted the same way, unless the foreign spread is overridden. In that case r_D,FC = r_f,FC + spread.
5. WACC = (1 − d) × r_E + d × r_D × (1 − t)

## Verification
The app's *Verification checks* panel confirms that:
- equal government bond yields produce equal USD and foreign results (when the foreign spread override is off)
- zero debt makes the WACC equal the cost of equity
- overriding the foreign-currency spread leaves the USD results unchanged

**Independent check** (defaults: Software (System & Application), U.S., β_U = 1.2254, t = 25.53%, ERP = 6.5%, d = 30%, r_f = 5.15%, spread = 1.11%):
- β_L = 1.2254 × (1 + 0.7447 × 0.4286) = 1.6165
- r_E = 5.15% + 1.6165 × 6.5% = 15.66%
- r_D after tax = 6.26% × (1 − 0.2553) = 4.66%
- WACC = 0.7 × 15.66% + 0.3 × 4.66% = **12.36%** (matches the app)

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy
Push `app.py`, `requirements.txt` and `README.md` to a GitHub repository. Then go to [share.streamlit.io](https://share.streamlit.io), click New app, select the repository, and set the main file to `app.py`.

## Notes
- Trading Economics sometimes serves a captcha to automated requests. If a yield can't be fetched, the app warns you and uses a manual input field.
- The 10Y government yield is used as the risk-free rate with no sovereign-default adjustment.
