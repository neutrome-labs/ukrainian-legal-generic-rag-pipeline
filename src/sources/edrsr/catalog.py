"""Discover official annual ЄДРСР ZIP exports through CKAN Action API."""

import re
from urllib.parse import urlsplit

import requests

ORGANIZATION = "derzhavna-sudova-administratsiia-ukrayiny"
TITLE = re.compile(r"Єдиний\s+державний\s+реєстр\s+судових\s+рішень\s+за\s+(\d{4})\s+рік\s*\.?", re.I)


class EdrsrCatalog:
    def __init__(self):
        self.session = requests.Session()

    def close(self):
        self.session.close()

    def discover(self) -> list[dict]:
        found = {}
        start = 0
        seen_packages = set()
        while True:
            response = self.session.get(
                "https://data.gov.ua/api/3/action/package_search",
                params={"q": '"Єдиний державний реєстр судових рішень"',
                        "fq": f"organization:{ORGANIZATION}", "rows": 100, "start": start,
                        "sort": "id asc"}, timeout=(10, 60),
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("success") is not True:
                raise ValueError(f"CKAN search failed: {payload.get('error')}")
            result = payload["result"]
            packages = result["results"]
            if not packages and start < result["count"]:
                raise ValueError("CKAN returned an empty page before the end")
            new_ids = {package["id"] for package in packages} - seen_packages
            if packages and not new_ids:
                raise ValueError("CKAN pagination did not advance")
            seen_packages.update(new_ids)
            for package in packages:
                match = TITLE.fullmatch(package.get("title", "").strip())
                if not match or package.get("organization", {}).get("name") != ORGANIZATION:
                    continue
                if package.get("private") or package.get("state") != "active":
                    continue
                year = int(match[1])
                resources = [resource for resource in package.get("resources", [])
                             if resource.get("state", "active") == "active"
                             and (resource.get("format", "").upper() == "ZIP"
                                  or urlsplit(resource.get("url", "")).path.lower().endswith(".zip"))]
                # Some years retain obsolete duplicate archives. Prefer the latest
                # last_modified resource; expose the selection policy in the listing.
                resources.sort(key=lambda item: item.get("last_modified") or "", reverse=True)
                if not resources:
                    raise ValueError(f"Dataset {package['id']} ({year}) has no ZIP resource")
                if len(resources) > 1 and resources[0].get("last_modified") == resources[1].get("last_modified"):
                    raise ValueError(f"Dataset {package['id']} ({year}) has ambiguous ZIP versions")
                resource = resources[0]
                entry = {
                    "year": year, "title": package["title"], "dataset_id": package["id"],
                    "dataset_url": f"https://data.gov.ua/dataset/{package['id']}",
                    "resource_id": resource["id"], "url": resource["url"],
                    "size": resource.get("size"), "last_modified": resource.get("last_modified"),
                    "license_id": package.get("license_id"),
                    "zip_resource_count": len(resources),
                    "selection": "latest last_modified ZIP resource", 
                }
                if year in found and found[year]["dataset_id"] != package["id"]:
                    raise ValueError(f"Multiple official datasets for {year}; inspect catalog before downloading")
                found[year] = entry
            start += len(packages)
            if start >= result["count"]:
                break
        return [found[year] for year in sorted(found)]
