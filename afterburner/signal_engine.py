"""Deterministic, candle-close-only analytical signals. NEVER places orders.

Scores are evidence indices, not calibrated success probabilities. A trigger is
NOT a simulated fill. Execution-quality gates apply to new entries.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from math import isfinite
from typing import Literal

STATES = frozenset({'FORMING','ARMED','TRIGGERED','PAPER_FILLED','CLOSED','EXPIRED','INVALIDATED','UNFILLED'})
ALLOWED = {
    'FORMING': {'ARMED','EXPIRED','INVALIDATED'},
    'ARMED': {'TRIGGERED','EXPIRED','INVALIDATED'},
    'TRIGGERED': {'PAPER_FILLED','UNFILLED','EXPIRED','INVALIDATED'},
    'PAPER_FILLED': {'CLOSED'},
    'CLOSED':set(), 'EXPIRED':set(), 'INVALIDATED':set(), 'UNFILLED':set(),
}
TIMEFRAMES = {'5m':300,'15m':900,'1h':3600}


def valid_candles(candles, interval='5m'):
    """Verify integrity and candle-close ordering; never use a live partial bar."""
    step = TIMEFRAMES[interval] * 1000
    out=[]
    for c in sorted(candles, key=lambda x: int(x['ts'])):
        try:
            t=int(c['ts']); o=float(c['open']); h=float(c['high']); l=float(c['low']); cl=float(c['close']); v=float(c['volume'])
        except (ValueError, TypeError, KeyError):
            continue
        if not all(map(isfinite,[o,h,l,cl,v])) or min(o,h,l,cl)<=0 or v<0 or l>min(o,cl) or h<max(o,cl):
            continue
        if out and t<=out[-1]['ts']:
            continue
        out.append({'ts':t,'open':o,'high':h,'low':l,'close':cl,'volume':v,'end':t+step-1})
    return out


def calculate_atr(candles, period=14):
    """Wilder smoothed ATR, using only completed bars supplied by the caller."""
    if period<=0 or len(candles)<period+1:
        return None
    tr=[]
    for prev, curr in zip(candles, candles[1:]):
        tr.append(max(curr['high']-curr['low'], abs(curr['high']-prev['close']), abs(curr['low']-prev['close'])))
    atr=sum(tr[:period])/period
    for value in tr[period:]:
        atr=(atr*(period-1)+value)/period
    return atr


def ema(values, period):
    if len(values)<period:
        return None
    result=sum(values[:period])/period
    a=2/(period+1)
    for v in values[period:]: result=a*v+(1-a)*result
    return result


def market_structure(candles, lookback=20):
    """Only past bars define levels; latest closed bar is reserved for the trigger."""
    if len(candles)<lookback+2:return None
    historic=candles[-(lookback+1):-1]
    highs=[x['high'] for x in historic]; lows=[x['low'] for x in historic]
    return {'resistance':max(highs),'support':min(lows), 'midpoint':(max(highs)+min(lows))/2}


def evidence_bias(candles, structure, volume_ratio_min=1.15):
    if len(candles)<51 or not structure:return ('WAIT', 0, ['insufficient_candles'])
    closes=[c['close'] for c in candles]
    fast=ema(closes, 12); slow=ema(closes, 26)
    recent=candles[-1]
    baseline=sum(c['volume'] for c in candles[-21:-1])/20
    volratio=recent['volume']/baseline if baseline>0 else 0
    if fast is None or slow is None:return ('WAIT',0,['insufficient_ema'])
    if recent['close']>structure['resistance'] and fast>slow and volratio>=volume_ratio_min:
        return ('LONG',min(95,50+min(30,int((volratio-1)*30))+15),['closed_breakout_above_resistance','ema12_above_ema26','volume_confirmation'])
    if recent['close']<structure['support'] and fast<slow and volratio>=volume_ratio_min:
        return ('SHORT',min(95,50+min(30,int((volratio-1)*30))+15),['closed_breakdown_below_support','ema12_below_ema26','volume_confirmation'])
    return ('WAIT',0,['no_confirmed_structure_break'])


def execution_gates(market, candles, now_ms, config, timeframe='5m'):
    risk=config.get('risk',{})
    problems=[]
    if not market.get('contract_active'):problems.append('contract_inactive_or_unknown')
    if market.get('price') is None:problems.append('missing_futures_price')
    spread=market.get('spread_pct')
    if spread is None or spread>risk.get('max_spread_pct',0.75):problems.append('wide_or_missing_spread')
    for k in ('bid_depth_usd','ask_depth_usd'):
        if market.get(k) is None or market[k]<risk.get('min_book_depth_usd_each_side',1500):problems.append('thin_or_missing_'+k)
    if market.get('volume_24h_usd') is None or market['volume_24h_usd']<risk.get('min_quote_volume_24h_usd',50000):problems.append('thin_or_missing_24h_volume')
    if len(candles)<100:problems.append('insufficient_candles')
    else:
        age=(now_ms-(candles[-1]['ts']+TIMEFRAMES[timeframe]*1000))/1000
        if age<0 or age>config.get('schedule',{}).get('max_market_age_seconds',600):problems.append('stale_or_future_candles')
        if any(b['ts']-a['ts']!=TIMEFRAMES[timeframe]*1000 for a,b in zip(candles[-51:],candles[-50:])):problems.append('gapped_candles')
    return sorted(set(problems))


def compute_levels(direction, close, atr, structure, config):
    if direction not in ('LONG','SHORT') or not atr or atr<=0 or not structure:return None
    cfg=config.get('signal',{})
    width=max(float(cfg.get('sl_atr_mult',1.5))*atr, abs(close-structure['support' if direction=='LONG' else 'resistance']))
    if width<=0:return None
    sign=1 if direction=='LONG' else -1
    stop=close-sign*width
    if stop<=0:return None
    tp1=close+sign*width*float(cfg.get('tp1_rr',2.0))
    tp2=close+sign*width*float(cfg.get('tp2_rr',3.0))
    # Conservative estimated round-trip costs, expressed as fraction of entry.
    spread_pct=float(cfg.get('assumed_spread_pct',0.0))
    fees_bps=float(cfg.get('fee_bps',10)); slippage_bps=float(cfg.get('slippage_bps',10))
    cost=close*((2*(fees_bps+slippage_bps)/10000)+(spread_pct/100))
    reward=abs(tp1-close)-cost; risk=abs(close-stop)+cost
    return {'entry_zone_low':close-atr*.15,'entry_zone_high':close+atr*.15,'entry_trigger':close,
            'sl':stop,'tp1':tp1,'tp2':tp2,'rr':round(reward/risk,3), 'risk_distance':width,
            'cost_estimate_price':cost}


def generate_signal(symbol, market, candles_by_tf, config, now_ms=None, timeframe='5m', research=None):
    """Return a signal candidate based on *completed* candles, or WAIT diagnostics.

    Only an explicit confirmed close breaks pre-existing S/R. No DEX leg is used
    unless an independently verified contract identity is added in the future.
    """
    if not config.get('signal',{}).get('enabled',True):
        return {'symbol':symbol,'direction':'WAIT','state':'FORMING','timeframe':timeframe,
                'confidence':0,'rationale':['signal_engine_disabled'],
                'blocking_reasons':['signal_engine_disabled'],'as_of_candle_ts':None,
                'strategy_version':'afterburner-0.4.0-breakout-v1','kronos_alignment':'unavailable'}
    now_ms=now_ms or int(datetime.now(timezone.utc).timestamp()*1000)
    raw=candles_by_tf.get(timeframe,[])
    candles=valid_candles(raw,timeframe)
    # Drop partially formed/future candles even when provider reports premature 'end'.
    candles=[c for c in candles if c['ts']+TIMEFRAMES[timeframe]*1000<=now_ms]
    gates=execution_gates(market,candles,now_ms,config,timeframe)
    structure=market_structure(candles)
    atr=calculate_atr(candles)
    direction, confidence, rationale=evidence_bias(candles,structure)
    base={'symbol':symbol,'direction':direction,'state':'FORMING' if direction=='WAIT' else 'ARMED',
          'timeframe':timeframe,'confidence':confidence,'rationale':rationale,
          'blocking_reasons':gates,'as_of_candle_ts':candles[-1]['ts'] if candles else None,
          'strategy_version':'afterburner-0.4.0-breakout-v1','kronos_alignment':'unavailable'}
    if research:
        base['market_intelligence']={
            'long_score':research.get('long_score'),
            'short_score':research.get('short_score'),
            'research_state':research.get('state'),
            'note':'Separate research context. Not a trigger or probability of success.'}
    if direction=='WAIT' or gates:
        base['state']='FORMING';return base
    levels=compute_levels(direction,candles[-1]['close'],atr,structure,config)
    if not levels or levels['rr']<float(config.get('signal',{}).get('min_net_rr',1.2)):
        base['blocking_reasons'].append('unacceptable_net_risk_reward');base['state']='FORMING';return base
    # Trigger is confirmed on this closing candle, but paper fill is a separate event.
    base.update(levels);base['state']='TRIGGERED';return base


def transition(current, target):
    if target not in STATES or target not in ALLOWED.get(current,set()):
        raise ValueError(f'Illegal signal transition {current} -> {target}')
    return target
