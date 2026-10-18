import unittest
from contextlib import ExitStack
from unittest.mock import Mock
from unittest.mock import patch

import sync_morning_epubs as sync


def block(id, type="page", title="", parent="", content=(), **kwargs):
    return sync.Block(id, type, title, "", content, parent, **kwargs)


class ResourceDiscoveryTests(unittest.TestCase):
    def test_canonical_event_names(self):
        self.assertEqual(sync.canonical_event_title("2026年秋季长老训练 亚伯拉罕、以撒、雅各的神"),
                         "2026年秋季长老负责弟兄训练 亚伯拉罕、以撒、雅各的神")
        self.assertEqual(sync.canonical_event_title("2026年春季长老及负责弟兄训练 主题"),
                         "2026年春季长老负责弟兄训练 主题")
        self.assertEqual(sync.canonical_event_title("2026年夏季训练 信徒（一）"),
                         "2026年夏季训练 信徒（一）")

    def test_native_title_repair_does_not_download_or_replace_epub(self):
        book = {"title": "2026年秋季长老训练 主题", "source": "notion-epub"}
        with patch.object(sync, "load_catalog", return_value={}), \
             patch.object(sync, "discover_latest_candidate", return_value=self.event), \
             patch.object(sync, "catalog_book", return_value=book), \
             patch.object(sync, "r2_client"), \
             patch.object(sync, "publish_catalog") as publish, \
             patch.object(sync, "epub_from_notion") as download, \
             patch.dict(sync.os.environ, {"R2_BUCKET": "test"}):
            self.assertEqual(sync.sync(dry_run=False), 1)
            publish.assert_called_once()
            download.assert_not_called()
            self.assertEqual(book["title"], "2026年秋季长老负责弟兄训练 主题")

    def setUp(self):
        self.event = sync.Candidate("2026.05", 2026, 5, "event", "event", "event")
        self.blocks = {
            "event": block("event", content=("db",)),
            "db": block("db", "collection_view", collection_id="collection", view_ids=("view",)),
        }

    def response(self, more=False, resource=True):
        ids = ["resource"] if resource else []
        return {
            "result": {"reducerResults": {"collection_group_results": {"blockIds": ids, "hasMore": more}}},
            "recordMap": {"block": {"resource": {"value": {"value": {
                "id": "resource", "type": "page", "parent_id": "collection",
                "properties": {"title": [["资源"]]},
            }}}}},
        }

    def test_direct_page_does_not_query_database(self):
        self.blocks["event"] = block("event", content=("resource",))
        self.blocks["resource"] = block("resource", title="资源")
        with patch.object(sync, "notion_post") as query:
            self.assertEqual(sync.find_resource_page(self.event, self.blocks).id, "resource")
            query.assert_not_called()

    def test_database_page(self):
        with patch.object(sync, "notion_post", return_value=self.response()) as query:
            self.assertEqual(sync.find_resource_page(self.event, self.blocks).id, "resource")
            self.assertEqual(query.call_args.args[0], "queryCollection")

    def test_expands_truncated_results(self):
        with patch.object(sync, "notion_post", side_effect=[self.response(True, False), self.response()]) as query:
            self.assertEqual(sync.find_resource_page(self.event, self.blocks).id, "resource")
            self.assertEqual(query.call_count, 2)

    def test_malformed_database_response_is_error(self):
        with patch.object(sync, "notion_post", return_value={}):
            with self.assertRaisesRegex(RuntimeError, "查询返回异常"):
                sync.find_resource_page(self.event, self.blocks)

    def test_missing_resource_is_not_missing_attachment(self):
        with patch.object(sync, "find_resource_page", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "未找到资源页"):
                sync.epub_from_notion(self.event, self.blocks)

    def test_resource_without_attachment_returns_none(self):
        with patch.object(sync, "find_resource_page", return_value=block("resource")), patch.object(sync, "load_notion_page", return_value={}):
            self.assertIsNone(sync.epub_from_notion(self.event, self.blocks))


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


if __name__ == "__main__":
    unittest.main()
