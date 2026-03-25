# 🔍 Angel One WebSocket Data Fields - Reality Check

## ❌ Critical Issue Found

**Problem**: Our code expects fields that Angel One WebSocket **DOES NOT provide**.

---

## 📊 What Angel One WebSocket V2 Actually Provides

Based on official documentation, Angel One WebSocket V2 provides **binary data** with these fields:

### LTP Mode (Mode 1) - Basic
- ✅ **Exchange Type** (NSE, BSE, MCX, etc.)
- ✅ **Token** (Instrument token)
- ✅ **Sequence Number**
- ✅ **Exchange Timestamp**
- ✅ **LTP** (Last Traded Price)

### QUOTE Mode (Mode 2) - More Data
- ✅ All LTP mode fields, plus:
- ✅ **Open, High, Low, Close** (OHLC)
- ✅ **Volume**
- ✅ **Open Interest** (OI)
- ✅ **ATP** (Average Traded Price)
- ✅ **Total Buy/Sell Quantity**
- ✅ **52-week High/Low**
- ✅ **Upper/Lower Circuit**

### SNAP_QUOTE Mode (Mode 3) - Best 5
- ✅ All QUOTE mode fields, plus:
- ✅ **Best 5 Bid Prices & Quantities**
- ✅ **Best 5 Ask Prices & Quantities**

---

## ❌ What Angel One WebSocket DOES NOT Provide

- ❌ **Delta** (Option Greek)
- ❌ **Gamma** (Option Greek)
- ❌ **Theta** (Option Greek)
- ❌ **Vega** (Option Greek)
- ❌ **Implied Volatility (IV)**

**These fields are NOT available in Angel One WebSocket!**

---

## 🔍 What VPS Actually Stores

Need to verify what the VPS data collector actually stores:

### Expected VPS Fields (from our schema):
```
symbol, token, ts, ltp, bid, ask, volume, oi, delta, gamma, theta, vega, iv, source
```

### Likely Reality:
- VPS probably stores: `ltp, volume, oi, source`
- VPS probably has NULL for: `delta, gamma, theta, vega, iv, bid, ask`

---

## ✅ Correct Data Mapping

### What We CAN Get from Angel One WebSocket:

| Field | Angel One Field | Available |
|-------|----------------|-----------|
| symbol | Token → Symbol mapping | ✅ Yes |
| token | token | ✅ Yes |
| ts | exchange_timestamp | ✅ Yes |
| ltp | ltp | ✅ Yes |
| volume | volume_trade_for_the_day | ✅ Yes (QUOTE mode) |
| oi | open_interest | ✅ Yes (QUOTE mode) |
| bid | best_5_buy_data[0].price | ✅ Yes (SNAP_QUOTE mode) |
| ask | best_5_sell_data[0].price | ✅ Yes (SNAP_QUOTE mode) |
| delta | N/A | ❌ Not available |
| gamma | N/A | ❌ Not available |
| theta | N/A | ❌ Not available |
| vega | N/A | ❌ Not available |
| iv | N/A | ❌ Not available |

---

## 🔧 Required Fixes

### 1. Update Subscription Mode
Change from LTP mode to **QUOTE mode** or **SNAP_QUOTE mode**:

```python
# In angel_connector.py
def on_open(self, ws):
    # Subscribe with QUOTE mode (2) or SNAP_QUOTE mode (3)
    for token_group in self.tokens:
        self.ws.subscribe(
            token_group["exchangeType"],
            token_group["tokens"],
            mode=2  # QUOTE mode for volume, OI
        )
```

### 2. Update Data Transformation
Remove expectations for Greeks and IV:

```python
def transform_angel_data(self, angel_data):
    return {
        'symbol': symbol,
        'token': token,
        'ts': datetime.now(timezone.utc).isoformat(),
        'ltp': angel_data.get('last_traded_price'),
        'bid': angel_data.get('best_5_buy_data', [{}])[0].get('price') if 'best_5_buy_data' in angel_data else None,
        'ask': angel_data.get('best_5_sell_data', [{}])[0].get('price') if 'best_5_sell_data' in angel_data else None,
        'volume': angel_data.get('volume_trade_for_the_day'),
        'oi': angel_data.get('open_interest'),
        'delta': None,  # NOT AVAILABLE
        'gamma': None,  # NOT AVAILABLE
        'theta': None,  # NOT AVAILABLE
        'vega': None,  # NOT AVAILABLE
        'iv': None,     # NOT AVAILABLE
        'source': 'angel_ws'
    }
```

### 3. Database Schema
Keep schema as-is (allows NULL for Greeks/IV), but understand:
- Greeks and IV will always be NULL from Angel One WebSocket
- If VPS has these fields, they come from a different source (API calls or calculations)

---

## 🎯 Recommendation

**Option 1**: Match VPS behavior exactly
- Store only what Angel One WebSocket provides
- Set Greeks/IV to NULL (same as VPS likely does)

**Option 2**: Calculate Greeks/IV separately
- Use Angel One REST API to fetch option chain with Greeks
- Requires additional API calls (slower, uses rate limits)
- Not real-time

**Option 3**: Use third-party Greeks calculator
- Calculate Greeks from option pricing models
- Requires: spot price, strike, expiry, volatility, interest rate
- Complex implementation

---

## ✅ Immediate Action

1. **Verify VPS data** - Check what VPS actually stores
2. **Update subscription mode** - Use QUOTE or SNAP_QUOTE mode
3. **Fix data transformation** - Match Angel One's actual fields
4. **Accept limitations** - Greeks/IV not available in real-time from WebSocket

---

**Conclusion**: Angel One WebSocket provides **price, volume, OI** but NOT **Greeks or IV**. We need to match this reality.
