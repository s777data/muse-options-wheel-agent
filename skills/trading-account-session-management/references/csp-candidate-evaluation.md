# CSP Candidate Evaluation Reference

## Quick Reference: CSP Filters and Thresholds

### Pre-screen (MCP scanner)
- Asset type: STOCK only
- Average volume (30d): > 500,000
- Market cap: $1B - $500B
- ATM IV (30d): 0.75 - 10.0
- Price: < $120 (runtime override: cash_balance / 100)
- RSI(14): < 55

### SEC Annual YoY Gate (strict)
- Growth > 15% (strictly greater)
- Source: SEC EDGAR Company Facts API
- Concepts: RevenueFromContractWithCustomerExcludingAssessedTax (preferred), Revenues, etc.
- Two most recent annual filings, 250-450 days each, 250-500 days apart
- Latest annual end ≤ 550 days before decision snapshot

### Post-screen Technical OR (at least one must MET)
1. SMA 50 distance ≤ 10%
2. SMA 200 distance ≤ 15%
3. Bollinger %B ≤ 0.35

### CSP Contract Selection (from screening.md reference)
| Filter | Threshold | Source |
|--------|-----------|--------|
| Short POP | 60% - 85% | Robinhood chance_of_profit_short |
| Net ROC | ≥ 8% | bid / strike (or mark / strike) |
| Bid-ask spread | ≤ 30% | (ask - bid) / midpoint |
| Open Interest | ≥ 100 | quote.open_interest |
| Daily Volume | ≥ 20 | quote.volume |
| DTE | 30-45 days | calendar days |
| Max candidates | 5 | at most 1 per ticker |

## Common Failure Patterns Observed

### Pattern 1: Low Premium / High ROC Conflict
For stocks trading well below $120 with high IV, OTM puts often have:
- **ROC < 8%** (premium too small relative to strike)
- **Short POP > 85%** (too safe, but ROC fails)

**Example:** ONDS $7.0 put — 79.5% POP but only 2.6% ROC

### Pattern 2: High ROC / Low POP Conflict  
ITM/ATM puts on volatile names:
- **ROC ≥ 8%** ✓
- **Short POP < 60%** ✗ (assignment risk too high)
- **Volume < 20** ✗ (liquidity risk)

**Example:** ONDS $8.5/$9.0 puts — 9.5%+ ROC but 59.5%/55.4% POP and 17/19 volume

### Pattern 3: RSI Filter Elimination
Many high-growth names fail RSI < 55:
- CRWV: RSI 56.3 ✗
- IOT: RSI 57.1 ✗  
- RBRK: RSI 60.0 ✗

**Only ONDS passed** with RSI 49.3 and Bollinger %B = 0.33

## ONDS Case Study (2026-08-26)

| Metric | Value |
|--------|-------|
| Price | $8.17 |
| RSI(14) | 49.3 ✓ |
| Avg Vol (30d) | 72.5M ✓ |
| SEC YoY | +605% ✓ |
| Tech OR | Bollinger %B = 0.33 ✓ |
| Price < $120 | ✓ |

**Put Chain (9/25 expiry, 30 DTE):**
| Strike | Bid | Ask | Mark | POP | ROC(bid) | ROC(mark) | OI | Vol | Spread% | PASS? |
|--------|-----|-----|------|-----|----------|-----------|-----|-----|---------|-------|
| 7.0 | 0.18 | 0.20 | 0.19 | 79.5% | 2.6% | 2.7% | 379 | 226 | 10.5% | ✗ ROC |
| 7.5 | 0.33 | 0.36 | 0.345 | 71.7% | 4.4% | 4.6% | 5,172 | 211 | 8.7% | ✗ ROC |
| 8.0 | 0.54 | 0.58 | 0.56 | 65.0% | 6.8% | 7.0% | 1,746 | 130 | 7.1% | ✗ ROC |
| 8.5 | 0.81 | 0.86 | 0.835 | **59.5%** | 9.5% | 9.8% | 738 | **17** | 6.0% | ✗ POP+Vol |
| 9.0 | 1.15 | 1.21 | 1.18 | **55.4%** | 12.8% | 13.1% | 509 | **19** | 5.1% | ✗ POP+Vol |

**Conclusion:** No qualifying CSP contracts for ONDS on this date.

## Debugging Checklist for Zero Results

1. **Verify scan returned symbols** → Check `total_items` from `create_scan`
2. **Check SEC YoY gate** → Query SEC EDGAR for each symbol
3. **Verify RSI calculation** → Need 15+ daily bars for Wilder RSI(14)
4. **Check SMA availability** → Need 50/200 bars for distance calcs
5. **Confirm Bollinger data** → Need 20 bars for %B
6. **Validate option chain** → DTE 30-45, multiplier=100, can_open_position=true
7. **Review quote freshness** → `updated_at` within `QUOTE_MAX_AGE_SECONDS` (1200s)

## Runtime Variable Override
`${MAX_UNDERLYING_PRICE_USD}` = floor(cash_available_for_collateral / 100)
- Used in MCP pre-screen `FILTER_TYPE_LAST` predicate `<`
- Overrides catalog default of $110
- Applied per AGENTS.md daily workflow