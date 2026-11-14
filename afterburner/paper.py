"""Auditable, conservative OHLCV paper-fill evaluation (no order API).

A triggered analytical signal does not imply a fill. The earliest simulated
entry is the next *completed* bar open, with assumed slippage. Missing time
segments invalidate simulation instead of assuming a favourable fill.
"""
from __future__ import annotations
from datetime import datetime,timezone
from .signal_engine import TIMEFRAMES


def iso_ms(timestamp):
    return datetime.fromtimestamp(timestamp/1000,timezone.utc).isoformat()


def paper_step(storage, symbol, bars, config):
    if not bars:return []
    cfg=config.get('signal',{}); events=[]
    slippage=float(cfg.get('slippage_bps',10))/10000
    fee=float(cfg.get('fee_bps',10))/10000
    tf=cfg.get('timeframe','5m');step=TIMEFRAMES[tf]*1000
    with storage.conn() as db:
        rows=db.execute("""SELECT s.* ,f.filled_at, f.fill_price
            FROM signals s LEFT JOIN paper_fills f ON f.signal_id=s.id
            WHERE s.symbol=? AND s.timeframe=? AND s.state IN ('TRIGGERED','PAPER_FILLED')
            ORDER BY s.id""",(symbol,tf)).fetchall()
        for row in rows:
            details=__import__('json').loads(row['details_json'])
            nextbars=[b for b in bars if b['ts']>row['as_of_candle_ts']]
            if not nextbars:continue
            cutoff=row['as_of_candle_ts']+step
            if nextbars[0]['ts']!=cutoff:
                if row['state']=='TRIGGERED':
                    db.execute("UPDATE signals SET state='UNFILLED',updated_at=? WHERE id=?",(iso_ms(nextbars[0]['ts']),row['id']))
                    db.execute('INSERT INTO signal_events(signal_id,event_type,occurred_at,price,note) VALUES(?,?,?,?,?)',
                               (row['id'],'UNFILLED',iso_ms(nextbars[0]['ts']),None,'Gap after trigger candle'))
                continue
            side=1 if row['direction']=='LONG' else -1
            if row['state']=='TRIGGERED':
                # Explicit next-bar-open approximation, with conservative adverse slippage.
                entry=nextbars[0]['open']*(1+side*slippage)
                target=details['tp1'];stop=details['sl']
                if side*(entry-stop)<=0 or side*(target-entry)<=0:
                    db.execute("UPDATE signals SET state='UNFILLED',updated_at=? WHERE id=?",(iso_ms(cutoff),row['id']))
                    db.execute('INSERT INTO signal_events(signal_id,event_type,occurred_at,price,note) VALUES(?,?,?,?,?)',
                               (row['id'],'UNFILLED',iso_ms(cutoff),entry,'Opening gap invalidates risk/reward'))
                    continue
                db.execute('INSERT INTO paper_fills(signal_id,filled_at,fill_price) VALUES(?,?,?)',
                           (row['id'],iso_ms(cutoff),entry))
                db.execute("UPDATE signals SET state='PAPER_FILLED',updated_at=? WHERE id=?",(iso_ms(cutoff),row['id']))
                db.execute('INSERT INTO signal_events(signal_id,event_type,occurred_at,price,note) VALUES(?,?,?,?,?)',
                           (row['id'],'PAPER_FILLED',iso_ms(cutoff),entry,'Hypothetical next-open fill, not real order'))
                events.append((row['id'],'PAPER_FILLED'))
            else:
                entry=row['fill_price']
            start_ms=int(datetime.fromisoformat(row['filled_at']).timestamp()*1000) if row['filled_at'] else cutoff
            # TP/SL can be hit on entry bar after the hypothetical opening fill.
            for b in nextbars:
                if b['ts']<start_ms:continue
                hit_sl=(b['low']<=details['sl'] if side==1 else b['high']>=details['sl'])
                hit_tp=(b['high']>=details['tp1'] if side==1 else b['low']<=details['tp1'])
                expired=b['ts']>=start_ms+int(cfg.get('expiry_seconds',14400))*1000
                if not (hit_sl or hit_tp or expired):continue
                reason='SL_OR_AMBIGUOUS' if hit_sl else 'TP1' if hit_tp else 'TIME_EXIT'
                reference=details['sl'] if hit_sl else details['tp1'] if hit_tp else b['close']
                exitprice=reference*(1-side*slippage)
                net=100*(side*(exitprice-entry)/entry-fee*(1+exitprice/entry))
                closed=iso_ms(b['ts']+step)
                db.execute("""UPDATE paper_fills SET closed_at=?,exit_price=?,exit_reason=?,net_pnl_pct=?
                    WHERE signal_id=?""",(closed,exitprice,reason,net,row['id']))
                db.execute("UPDATE signals SET state='CLOSED',updated_at=? WHERE id=?",(closed,row['id']))
                db.execute('INSERT INTO signal_events(signal_id,event_type,occurred_at,price,note) VALUES(?,?,?,?,?)',
                           (row['id'],'CLOSED',closed,exitprice,reason))
                events.append((row['id'],'CLOSED'))
                break
    return events
