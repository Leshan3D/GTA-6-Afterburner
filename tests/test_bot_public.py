"""Offline regression tests for Telegram access, operational reports, and public-surface isolation."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import json
import os
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from afterburner.alerts import Telegram, parse_command
from afterburner.config import assets, settings
from afterburner.public import public_payload, export_public_site, build_public_server
from afterburner.reporting import status_report
from afterburner.storage import Storage, iso
from threading import Thread


class FakeResponse:
    def __init__(self, data):
        self.data = data
    def json(self):
        return self.data
    def raise_for_status(self):
        return None


class TestBotPublic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Storage(Path(self.tmp.name) / 'afterburner.db')
        self.assets = assets()
        self.config = settings()

    def add_sample(self):
        self.storage.save_snapshot(
            self.assets[0].symbol,
            {'price': .007, 'source_health': {'PRIVATE_API_TOKEN': 'must_not_leak'},
             'change_24h_pct': -12.0, 'contract_active': True,
             'volume_24h_usd': 500_000, 'funding_rate_pct': .03,
             'spread_pct': .3, 'open_interest_usd': 900_000},
            {'primary': {'liquidity_usd': 120_000}, 'private': 'secret'},
            {'state': 'MONITOR', 'long_score': 28, 'short_score': 55,
             'blocking_flags': ['dex_to_futures_identity_unverified'],
             'other_private_state': 'PRIVATE_OPS'},
        )

    def test_commands_handle_mentions_and_reject_wrong_bot(self):
        self.assertEqual(parse_command('/meme@AfterburnerBot GATO', 'AfterburnerBot'), '/meme GATO')
        self.assertEqual(parse_command('/progress'), '/progress')
        self.assertEqual(parse_command('/start@AfterburnerBot', 'AfterburnerBot'), '/start')
        self.assertIsNone(parse_command('/meme@SomeoneElse GATO', 'AfterburnerBot'))
        self.assertIsNone(parse_command('/positions'))
        self.assertIsNone(parse_command('hello /progress'))
        self.assertIsNone(parse_command('/meme@Bot GATO', ''))

    def test_operational_report_has_real_coverage_and_missing_prices(self):
        self.add_sample()
        self.storage.put_kv('market_last_run', iso())
        self.storage.set_health('bingx:GATO-USDT', 'ok')
        self.storage.set_health('news:gaming', 'error', 'failed API')
        text = status_report(self.assets, self.storage, self.config, '/progress')
        self.assertIn('Fresh markets: 1/4', text)
        self.assertIn('1/2 healthy; 1 degraded', text)
        self.assertIn('Execution: DISABLED', text)
        self.assertIn('DEX pool: $120,000', status_report(self.assets, self.storage, self.config, '/meme GATO'))
        self.assertIn('awaiting first sample', status_report(self.assets, self.storage, self.config, '/meme'))
        self.assertIn('news:gaming', status_report(self.assets, self.storage, self.config, '/health'))
        self.assertIn('Unknown token', status_report(self.assets, self.storage, self.config, '/meme XYZ'))

    def test_telegram_allowlist_and_replies(self):
        env = {'AFTERBURNER_TELEGRAM_BOT_TOKEN':'synthetic-secret',
               'AFTERBURNER_TELEGRAM_CHAT_ID':'123',
               'AFTERBURNER_TELEGRAM_ALLOWED_CHAT_IDS':'456, 789',
               'AFTERBURNER_TELEGRAM_BOT_USERNAME':'AfterburnerBot',
               'AFTERBURNER_NO_ALERTS':'1'}
        responses = [
            {'update_id':1,'message':{'chat':{'id':999},'text':'/progress'}},
            {'update_id':2,'message':{'chat':{'id':456},'text':'/progress'}},
            {'update_id':3,'message':{'chat':{'id':123},'text':'/meme@SomeoneElse GATO'}},
            {'update_id':4,'message':{'chat':{'id':123},'text':'/meme@AfterburnerBot GATO'}},
        ]
        with patch.dict(os.environ, env, clear=True):
            bot = Telegram(self.storage)
            self.assertTrue(bot.enabled)
            self.assertFalse(bot.push_enabled) # push off, private replies still work
            with patch('afterburner.alerts.requests.get', return_value=FakeResponse({'ok':True,'result':responses})), \
                 patch('afterburner.alerts.requests.post', return_value=FakeResponse({'ok':True})) as post:
                handled = bot.poll_once(lambda cmd: 'RESULT '+cmd)
                self.assertEqual(handled, 2)
                self.assertEqual(post.call_count, 2)
                self.assertEqual(post.call_args_list[0].kwargs['json']['chat_id'], '456')
                self.assertEqual(post.call_args_list[1].kwargs['json']['chat_id'], '123')
                self.assertEqual(self.storage.get_kv('telegram_offset'), '5')
                self.assertFalse(bot.send_to('999', 'Private report'))
                self.assertEqual(post.call_count, 2)

    def test_public_snapshot_excludes_private_state(self):
        self.add_sample()
        self.storage.set_health('bingx:GATO-USDT', 'error', 'do not publish HEALTH_ERROR_SECRET')
        self.storage.put_kv('telegram_offset', 'PRIVATE_CHAT_ID')
        payload = public_payload(self.storage, self.assets, self.config)
        raw = json.dumps(payload)
        for forbidden in ('PRIVATE_API_TOKEN', 'must_not_leak', 'PRIVATE_OPS',
                          'HEALTH_ERROR_SECRET', 'PRIVATE_CHAT_ID', 'telegram_offset', 'source_health'):
            self.assertNotIn(forbidden, raw)
        self.assertFalse(payload['execution_enabled'])
        self.assertEqual(len(payload['assets']), 4)
        self.assertEqual(payload['assets'][0]['price'], .007)
        self.assertIsNone(payload['assets'][1]['price'])
        out = export_public_site(self.storage, self.assets, self.config,
                                 Path(self.tmp.name) / 'site')
        self.assertTrue((out / 'index.html').is_file())
        self.assertTrue((out / '.nojekyll').is_file())
        self.assertEqual(json.loads((out / 'data.json').read_text())['assets'][0]['ticker'], 'GATO')

    def test_public_preview_cannot_access_private_endpoints(self):
        self.add_sample()
        server = build_public_server(self.storage, self.assets,
                                     {**self.config, 'public': {'host':'127.0.0.1','port':0}})
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = 'http://127.0.0.1:' + str(server.server_port)
        with urlopen(base + '/data.json', timeout=2) as response:
            body = json.loads(response.read())
            self.assertEqual(body['assets'][0]['ticker'], 'GATO')
        with urlopen(base + '/', timeout=2) as response:
            self.assertIn(b'AFTERBURNER', response.read())
        for private_route in ('/api/health', '/api/proposals', '/api/assets', '/.env'):
            with self.assertRaises(HTTPError) as error:
                urlopen(base + private_route, timeout=2)
            self.assertEqual(error.exception.code, 404)

    def test_private_dashboard_port_rejected_for_public(self):
        with self.assertRaises(ValueError):
            build_public_server(self.storage, self.assets,
                                {**self.config, 'public': {'host':'0.0.0.0','port':5057}})
        with self.assertRaises(ValueError):
            build_public_server(self.storage, self.assets,
                                {**self.config, 'public': {'host':'127.0.0.1','port':5056}})


if __name__ == '__main__':
    unittest.main()
