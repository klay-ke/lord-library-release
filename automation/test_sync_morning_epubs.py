import unittest
from contextlib import ExitStack
from unittest.mock import patch, Mock
import sync_morning_epubs as sync


class NotionOnlySyncTests(unittest.TestCase):
    def run_case(self, existing, resource):
        catalog = {'books': []}
        client = Mock()
        event = sync.Candidate('2026.05', 2026, 5, 'test-page', 'event', '秋季长老训练')
        with ExitStack() as stack:
            def mock(name, **kwargs):
                return stack.enter_context(patch.object(sync, name, **kwargs))
            mock('load_catalog', return_value=catalog)
            mock('discover_latest_candidate', return_value=event)
            mock('catalog_book', return_value=existing)
            mock('load_notion_page', return_value={})
            mock('page_topic', return_value='')
            mock('epub_from_notion', return_value=resource)
            r2 = mock('r2_client', return_value=client)
            mock('validate_epub', return_value={})
            mock('epub_topic', return_value='')
            mock('verify_public_object')
            add = mock('add_catalog_book')
            def finalize(c):
                c['contentHash'] = 'test'
                return b'{}'
            mock('finalize_catalog', side_effect=finalize)
            network = mock('request_bytes', side_effect=AssertionError('unexpected fallback request'))
            stack.enter_context(patch.dict(sync.os.environ, {'R2_BUCKET': 'test'}))
            result = sync.sync(dry_run=False)
            network.assert_not_called()
            if resource is None:
                self.assertEqual(result, 0)
                r2.assert_not_called()
                client.put_object.assert_not_called()
                add.assert_not_called()
                self.assertEqual(catalog, {'books': []})
            else:
                self.assertEqual(result, 1)
                self.assertEqual(client.put_object.call_count, 3)
                self.assertEqual(add.call_args.args[1]['source'], resource[1])
                self.assertTrue(add.call_args.args[1]['sourceURL'].startswith('https://www.notion.so/'))

    def test_new_volume_without_epub_keeps_catalog(self):
        self.run_case(None, None)

    def test_generated_volume_without_epub_is_preserved(self):
        self.run_case({'source': 'stemofjesse-html'}, None)

    def test_notion_epub_still_publishes(self):
        self.run_case(None, (b'native epub', 'notion-epub'))

    def test_notion_zip_still_publishes(self):
        self.run_case(None, (b'zip epub', 'notion-zip'))

if __name__ == '__main__':
    unittest.main()
