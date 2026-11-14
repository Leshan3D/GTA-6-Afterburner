"""Simple conservative walk-forward candle-close breakout research backtest.

Not fills in a real order book. Costs and stops are hypotheses. Signals are
calculated only from candles that have closed; fills occur next bar, never same.
"""
from __future__ import annotations
from .signal_engine import generate_signal, valid_candles


def backtest(symbol, candles, config, train_fraction=.6, timeframe='5m'):
    bars=valid_candles(candles,timeframe)
    if len(bars)<160:
        return {'status':'insufficient_real_history','bars':len(bars),'trades':0}
    split=max(101,int(len(bars)*train_fraction))
    cfg=config.get('signal',{})
    fee=float(cfg.get('fee_bps',10))/10000
    slip=float(cfg.get('slippage_bps',10))/10000
    results=[]; equity=1.0; peak=1.0; maxdd=0.0
    # We deliberately do not optimize model parameters on the test set.
    i=split
    while i<len(bars)-1:
        # Backtesting evaluates OHLCV only; historical order-book quality is
        # unavailable. Therefore never classify this result as deployable.
        market={'contract_active':True,'price':bars[i]['close'],'spread_pct':0,
                'bid_depth_usd':1e20,'ask_depth_usd':1e20,'volume_24h_usd':1e20}
        history=bars[:i+1]
        t=bars[i]['ts']+300000 if timeframe=='5m' else bars[i]['ts']+{'15m':900000,'1h':3600000}[timeframe]
        local=dict(config); local['schedule']={**config.get('schedule',{}),'max_market_age_seconds':600}
        result=generate_signal(symbol,market,{timeframe:history},local,now_ms=t,timeframe=timeframe)
        if result.get('state')!='TRIGGERED':
            i+=1;continue
        # Model a next-open market fill, not automatic fill at trigger close.
        side=1 if result['direction']=='LONG' else -1
        entry=bars[i+1]['open']*(1+side*slip)
        stop=result['sl']; tp=result['tp1']
        # Reject if gap already invalidated stop or was beyond target.
        if side*(entry-stop)<=0 or side*(tp-entry)<=0:
            i+=1;continue
        exitprice=None; reason=''; j=i+1
        expiry=max(1, int(cfg.get('expiry_seconds',14400)/({'5m':300,'15m':900,'1h':3600}[timeframe])))
        while j<len(bars) and j<=i+expiry:
            bar=bars[j]
            hit_stop=bar['low']<=stop if side==1 else bar['high']>=stop
            hit_tp=bar['high']>=tp if side==1 else bar['low']<=tp
            if hit_stop:
                exitprice=stop*(1-side*slip);reason='stop_or_ambiguous';break
            if hit_tp:
                exitprice=tp*(1-side*slip);reason='tp1';break
            j+=1
        if exitprice is None:
            j=min(j,len(bars)-1)
            exitprice=bars[j]['close']*(1-side*slip);reason='time_exit'
        # Both entry and exit pay taker fee as a conservative baseline.
        gross=side*(exitprice-entry)/entry
        net=gross-fee*(1+exitprice/entry)
        equity*=max(0,1+net);peak=max(equity,peak);maxdd=max(maxdd,1-equity/peak)
        results.append({'signal_ts':bars[i]['ts'],'fill_ts':bars[i+1]['ts'],
                        'exit_ts':bars[j]['ts'],'direction':result['direction'],
                        'net_return_pct':round(net*100,5),'exit_reason':reason})
        i=max(i+1,j+1)
    winners=[r for r in results if r['net_return_pct']>0]
    return {'status':'historical_simulation_only', 'symbol':symbol,'timeframe':timeframe,
            'test_bars':len(bars)-split,'total_bars':len(bars),'trades':len(results),
            'win_rate_pct':round(100*len(winners)/len(results),2) if results else None,
            'return_pct':round((equity-1)*100,3),'max_drawdown_pct':round(maxdd*100,3),
            'assumptions':'Next-bar-open entry, inferred historical book quality, worst-case same-candle SL/TP, fixed fee/slippage, no funding',
            'trades_sample':results[-15:]}
