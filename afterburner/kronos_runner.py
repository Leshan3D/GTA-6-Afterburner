"""Optional isolated Kronos subprocess; stdout is one JSON document only.

Install into a dedicated CPU venv with the official upstream Kronos package
made importable as `model` (or `kronos_src.model` if vendored). Never imports
or reads JAFAR paths. Download weights only with operator permission.
"""
import json
import sys
from datetime import datetime, timezone


def main():
    payload=json.load(sys.stdin)
    import pandas as pd
    import torch
    try:
        from model import Kronos, KronosTokenizer, KronosPredictor
    except ImportError:
        from kronos_src.model import Kronos, KronosTokenizer, KronosPredictor
    candle=payload['candles']; tf=payload['timeframe'];period={'5m':300,'15m':900,'1h':3600}[tf]
    frame=pd.DataFrame([{'open':c['open'],'high':c['high'],'low':c['low'],
                         'close':c['close'],'volume':c['volume'],
                         'amount':c['close']*c['volume']} for c in candle])
    timestamps=pd.Series(pd.to_datetime([c['ts'] for c in candle],unit='ms',utc=True).tz_localize(None))
    horizon=payload['horizon_bars'];cutoff=candle[-1]['ts']
    future=pd.Series(pd.to_datetime([cutoff+1000*period*(i+1) for i in range(horizon)],unit='ms',utc=True).tz_localize(None))
    torch.manual_seed(42)
    model=Kronos.from_pretrained(payload['model']);tokenizer=KronosTokenizer.from_pretrained('NeoQuasar/Kronos-Tokenizer-base')
    model.eval();tokenizer.eval()
    predictor=KronosPredictor(model,tokenizer,device='cpu',max_context=min(512,len(candle)))
    with torch.no_grad():
        pred=predictor.predict(df=frame,x_timestamp=timestamps,y_timestamp=future,
            pred_len=horizon,sample_count=payload.get('sample_count',1),T=1.0,top_p=0.9)
    closes=[float(x) for x in pred['close'].tolist()]
    if len(closes)!=horizon:raise ValueError('Incomplete forecast')
    delta=100*(closes[-1]/float(candle[-1]['close'])-1)
    result={'status':'ok','symbol':payload['symbol'],'timeframe':tf,'cutoff_ts':cutoff,
            'horizon_bars':horizon,'model_version':payload['model'],'predicted_closes':closes,
            'forecast_return_pct':delta,'directional_bias':'LONG' if delta>0 else 'SHORT' if delta<0 else 'NEUTRAL',
            'created_at':datetime.now(timezone.utc).isoformat()}
    print(json.dumps(result,allow_nan=False))


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('Kronos subprocess failed: '+type(exc).__name__,file=sys.stderr)
        sys.exit(2)
