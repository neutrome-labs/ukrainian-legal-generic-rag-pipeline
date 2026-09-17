from unittest.mock import Mock

import pytest

from src.sources.edrsr.catalog import EdrsrCatalog, ORGANIZATION


def package(year=2025):
    return {"id": f"dataset-{year}", "title": f"Єдиний державний реєстр судових рішень за {year} рік.",
            "organization": {"name": ORGANIZATION}, "state": "active", "private": False,
            "resources": [{"id": "zip", "format": "ZIP", "last_modified": "2026-01-01",
                           "url": "https://data.gov.ua/file.zip", "size": 123},
                          {"id": "pdf", "format": "PDF", "url": "https://data.gov.ua/readme.pdf"}]}


def response(packages, count):
    result = Mock()
    result.json.return_value = {"success": True, "result": {"count": count, "results": packages}}
    return result


def test_paginated_discovery():
    catalog = EdrsrCatalog()
    catalog.session.get = Mock(side_effect=[response([package(2024)], 2), response([package()], 2)])
    assert [item["year"] for item in catalog.discover()] == [2024, 2025]
    assert catalog.session.get.call_args_list[1].kwargs["params"]["start"] == 1
    catalog.close()


def test_filters_title_and_publisher_and_selects_latest():
    wrong_title = {**package(2023), "title": "Інша інформація за 2023 рік"}
    wrong_publisher = {**package(2022), "organization": {"name": "other"}}
    valid = package()
    valid["resources"].append({"id": "old", "format": "ZIP", "url": "https://data.gov.ua/old.zip", "last_modified": "2021-01-01"})
    catalog = EdrsrCatalog()
    catalog.session.get = Mock(return_value=response([wrong_title, wrong_publisher, valid], 3))
    result = catalog.discover()
    assert len(result) == 1
    assert result[0]["resource_id"] == "zip"
    assert result[0]["zip_resource_count"] == 2
    catalog.close()


def test_ckan_error():
    catalog = EdrsrCatalog()
    result = Mock()
    result.json.return_value = {"success": False, "error": "test"}
    catalog.session.get = Mock(return_value=result)
    with pytest.raises(ValueError, match="CKAN search failed"):
        catalog.discover()
    catalog.close()


def test_missing_zip():
    item = package()
    item["resources"] = []
    catalog = EdrsrCatalog()
    catalog.session.get = Mock(return_value=response([item], 1))
    with pytest.raises(ValueError, match="no ZIP"):
        catalog.discover()
    catalog.close()
