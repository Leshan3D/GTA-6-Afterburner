"""Optional fail-open subprocess adapter. Kronos never votes or vetoes signals."""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path
from .signal_engine import TIMEFRAMES, valid_candles


def forecast(symbol, timeframe, candles, config, runner_python=None):
    conf=config.get('kronos',{})
    if not conf.get('enabled',False):
        return {'status':'disabled','symbol':symbol,'timeframe':timeframe,'directional_weight':0.0}
    if float(conf.get('factor_weight',0)) != 0:
        # Intentionally reject nonzero influence until statistically justified.
        return {'status':'invalid_config_weight_must_be_zero','symbol':symbol,'timeframe':timeframe}
    rows=valid_candles(candles,timeframe)
    window=int(conf.get('lookback',400))
    if len(rows)<window:
        return {'status':'insufficient_history','available_bars':len(rows),'required_bars':window,
                'symbol':symbol,'timeframe':timeframe}
    runner=Path(__file__).with_name('kronos_runner.py')
    python_bin=str(runner_python or conf.get('python') or sys.executable)
    payload={'symbol':symbol,'timeframe':timeframe,'candles':rows[-window:],
             'horizon_bars':int(conf.get('horizons',{}).get(timeframe,6)),
             'model':str(conf.get('model','NeoQuasar/Kronos-small')),
             'sample_count':int(conf.get('samples',1))}
    try:
        output=subprocess.run([python_bin,str(runner)],input=json.dumps(payload),
            text=True,capture_output=True,timeout=int(conf.get('timeout_seconds',120)),check=False)
        if output.returncode:
            return {'status':'runner_failed','symbol':symbol,'timeframe':timeframe,
                    'reason':'Model runner failed; inspect local service logs'}
        result=json.loads(output.stdout)
        if not isinstance(result,dict) or result.get('symbol')!=symbol:
            raise ValueError('Invalid Kronos runner output')
        result['directional_weight']=0.0
        return result
    except (OSError,ValueError,subprocess.TimeoutExpired,json.JSONDecodeError):
        return {'status':'unavailable','symbol':symbol,'timeframe':timeframe,'directional_weight':0.0}
