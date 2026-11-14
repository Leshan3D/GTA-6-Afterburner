"""Publisher correctness tests: no live GitHub access required."""
from __future__ import annotations
import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from afterburner.publisher import (publishable_difference, validate_payload,
                                    publisher_run, write_status, status_file)


class PublisherTest(TestCase):
    def sample(self):
        from afterburner.config import assets, settings
        from afterburner.public import public_payload
        from afterburner.storage import Storage
        with tempfile.TemporaryDirectory() as tmp:
            store = Storage(Path(tmp) / 'radar.db')
            return public_payload(store, assets(), settings())

    def test_explicit_schema_and_private_field_rejection(self):
        data = self.sample()
        symbols = [x['symbol'] for x in data['assets']]
        self.assertTrue(validate_payload(data, symbols))
        data['telegram_token'] = 'SECRET'
        with self.assertRaises(ValueError):
            validate_payload(data, symbols)
        del data['telegram_token']
        data['assets'][0]['internal_path'] = '/private/path'
        with self.assertRaises(ValueError):
            validate_payload(data, symbols)

    def test_duplicate_and_incomplete_markets_blocked(self):
        data = self.sample()
        symbols = [x['symbol'] for x in data['assets']]
        data['assets'].pop()
        with self.assertRaises(ValueError):
            validate_payload(data, symbols)

    def test_no_change_suppresses_clock_only_difference(self):
        data = self.sample()
        other = json.loads(json.dumps(data))
        other['generated_at'] = '2040-01-01T00:00:00+00:00'
        for a in other['assets']:
            a['stale_at_export'] = not a['stale_at_export']
        self.assertFalse(publishable_difference(data, other))
        other['assets'][0]['price'] = 0.005
        self.assertTrue(publishable_difference(data, other))

    def test_repository_must_be_pinned(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'pinned'):
                publisher_run(Path(tmp), repo='https://github.com/other/repo.git')

    def test_status_atomic_no_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_status(root, state='error', error='No network')
            write_status(root, state='pushed', last_commit='abcd')
            state = json.loads(status_file(root).read_text())
            self.assertEqual(state['last_commit'], 'abcd')
            self.assertEqual(state['state'], 'pushed')
            self.assertEqual(status_file(root).stat().st_mode & 0o777, 0o600)

    def test_publisher_lock_prevents_second_runner(self):
        import fcntl
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / 'publisher.lock'
            with lock.open('a+') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertEqual(publisher_run(tmp), 'already running')

    def test_git_failure_not_exposed_as_raw_stderr(self):
        from afterburner.publisher import git
        from unittest.mock import Mock
        fake = Mock(returncode=1, stderr='SECRET_PRIVATE_CREDENTIAL', stdout='')
        with patch('afterburner.publisher.subprocess.run', return_value=fake):
            with self.assertRaises(RuntimeError) as err:
                git('.', 'push', 'origin', 'HEAD:main')
            self.assertNotIn('SECRET_PRIVATE_CREDENTIAL', str(err.exception))
