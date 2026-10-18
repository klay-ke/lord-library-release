import unittest
from unittest.mock import patch

import sync_morning_epubs as sync


def block(id, type="page", title="", parent="", content=(), **kwargs):
    return sync.Block(id, type, title, "", content, parent, **kwargs)


class ResourceDiscoveryTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
