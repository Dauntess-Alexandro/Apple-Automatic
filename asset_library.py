import concurrent.futures
import hashlib
import os
import time
from urllib.parse import urlencode, urlparse

import requests
from PIL import Image


DISPLAY_CLASSES = {
    "APP_IPHONE_61": "IPHONE_DYNAMIC_ISLAND_MEDIUM_DISPLAY",
    "APP_IPHONE_67": "IPHONE_DYNAMIC_ISLAND_LARGE_DISPLAY",
    "APP_IPHONE_65": "IPHONE_FACE_ID_LARGE_DISPLAY",
    "APP_IPHONE_58": "IPHONE_FACE_ID_MEDIUM_DISPLAY",
    "APP_IPHONE_55": "IPHONE_HOME_BUTTON_LARGE_DISPLAY",
    "APP_IPHONE_47": "IPHONE_HOME_BUTTON_MEDIUM_DISPLAY",
    "APP_IPHONE_40": "IPHONE_HOME_BUTTON_40_DISPLAY",
}
READY_IMAGE_STATES = {
    "PREPARE_FOR_SUBMISSION", "READY_FOR_REVIEW", "WAITING_FOR_REVIEW",
    "IN_REVIEW", "ACCEPTED", "APPROVED",
}
READY_PLACEMENT_STATES = {"ACTIVE"}
IOS_SCREENSHOT_PLATFORMS = {"IPHONE_APP_STORE", "IPAD_APP_STORE"}
SURFACE_TYPES = {
    "appStoreVersionLocalization": "appStoreVersionLocalizations",
    "appStoreVersionExperimentTreatmentLocalization": "appStoreVersionExperimentTreatmentLocalizations",
}


class ScreenshotAssetCatalog:
    def __init__(self, response):
        records = response.get("data") or []
        if not isinstance(records, list) or not records:
            raise ValueError("Apple не вернула каталог Asset Library. Старые скриншоты не изменены.")
        self.attributes = records[0].get("attributes") or {}
        self.specs = {item["specId"]: item for item in self.attributes.get("imageSpecs") or []}
        self.groups = {item["placementProfileGroupId"]: item for item in self.attributes.get("placementProfileGroups") or []}

    def screenshot_groups(self, feature):
        feature_info = next((item for item in self.attributes.get("features") or [] if item.get("featureId") == feature), None)
        if not feature_info:
            raise ValueError(f"Apple не разрешает Asset Library для режима {feature}.")
        placement_type = next((item for item in self.attributes.get("placementTypes") or []
                               if item.get("placementTypeId") == "APP_SCREENSHOT"), {})
        if "APP_SCREENSHOTS_AND_PREVIEWS" not in placement_type.get("acceptsAssetCategories", []):
            raise ValueError("В каталоге Apple нет поддерживаемого типа скриншотов.")
        limits = {}
        for policy in feature_info.get("placementPolicies") or []:
            if policy.get("placementType") == "APP_SCREENSHOT":
                for limit in policy.get("groupLimits") or []:
                    for group_id in limit.get("groupIds") or []:
                        limits[group_id] = min(limits.get(group_id, 10), limit.get("maxCount", 0), 10)
        groups = {}
        for mapping in placement_type.get("specMappings") or []:
            group_id = mapping.get("placementGroupId")
            if (group_id not in self.groups or limits.get(group_id, 0) <= 0
                    or self.groups[group_id].get("platform") not in IOS_SCREENSHOT_PLATFORMS):
                continue
            spec_ids = set(mapping.get("specs") or []) & self.specs.keys()
            if spec_ids:
                group = groups.setdefault(group_id, {**self.groups[group_id], "spec_ids": set(), "max_count": limits[group_id]})
                group["spec_ids"].update(spec_ids)
        return groups

    def validate_existing_image(self, resource, group):
        attributes = resource.get("attributes") or {}
        identifier = resource.get("id", "?")
        label = f"{attributes.get('fileName', '?')} [{identifier}]"
        if attributes.get("state") not in READY_IMAGE_STATES:
            raise ValueError(f"{label}: ассет не готов — {attributes.get('state')}: {attributes.get('stateDetails') or attributes.get('invalidReasons') or ''}")
        if attributes.get("category") != "APP_SCREENSHOTS_AND_PREVIEWS" or attributes.get("specId") not in group["spec_ids"]:
            raise ValueError(f"{label}: категория/спецификация не соответствует выбранной группе Apple.")
        spec = self.specs[attributes["specId"]]
        dimensions = spec.get("dimensions") or {}
        image_asset = attributes.get("imageAsset") or {}
        for axis in ("Width", "Height"):
            value = image_asset.get(axis.lower())
            lower, upper = dimensions.get(f"min{axis}"), dimensions.get(f"max{axis}")
            if not isinstance(value, (int, float)) or lower is None or upper is None or not lower <= value <= upper:
                raise ValueError(f"{label}: размеры не соответствуют спецификации Apple.")

    def match(self, path, display_type, feature):
        feature_info = next((item for item in self.attributes.get("features") or [] if item.get("featureId") == feature), None)
        if not feature_info:
            raise ValueError(f"Apple не разрешает Asset Library для режима {feature}.")
        screenshot_type = next((item for item in self.attributes.get("placementTypes") or []
                                if item.get("placementTypeId") == "APP_SCREENSHOT"), {})
        if "APP_SCREENSHOTS_AND_PREVIEWS" not in screenshot_type.get("acceptsAssetCategories", []):
            raise ValueError("В каталоге Apple нет поддерживаемого типа скриншотов.")
        limits = {}
        for policy in feature_info.get("placementPolicies") or []:
            if policy.get("placementType") == "APP_SCREENSHOT":
                for limit in policy.get("groupLimits") or []:
                    for group_id in limit.get("groupIds") or []:
                        limits[group_id] = min(limits.get(group_id, limit["maxCount"]), limit["maxCount"])
        with Image.open(path) as image:
            image.load()
            width, height = image.size
            alpha = "A" in image.getbands() or "transparency" in image.info
            mime_type = Image.MIME.get(image.format)
        extension = os.path.splitext(path)[1].lower()
        file_size = os.path.getsize(path)
        for mapping in screenshot_type.get("specMappings") or []:
            group_id = mapping.get("placementGroupId")
            group = self.groups.get(group_id, {})
            if group.get("displayClassId") != DISPLAY_CLASSES.get(display_type) or group.get("platform") != "IPHONE_APP_STORE":
                continue
            if group_id not in limits or limits[group_id] <= 0:
                continue
            matches = set()
            for spec_id in mapping.get("specs") or []:
                spec = self.specs.get(spec_id, {})
                dimensions = spec.get("dimensions") or {}
                bounds = [dimensions.get(key) for key in ("minWidth", "maxWidth", "minHeight", "maxHeight")]
                if any(bound is None for bound in bounds) or not (bounds[0] <= width <= bounds[1] and bounds[2] <= height <= bounds[3]):
                    continue
                if extension not in spec.get("fileExtensions", []) or mime_type not in spec.get("mimeTypes", []):
                    continue
                if alpha and not spec.get("alphaAllowed", False):
                    raise ValueError(f"{os.path.basename(path)}: альфа-канал/прозрачность не разрешены Apple.")
                if file_size <= 0 or file_size > spec.get("maxFileSize", 0):
                    raise ValueError(f"{os.path.basename(path)}: размер файла превышает лимит Apple.")
                matches.add(spec_id)
            if matches:
                return {"group": group_id, "spec_ids": matches, "max_count": limits[group_id]}
        raise ValueError(f"{os.path.basename(path)} ({width}×{height}): нет подходящей спецификации Apple для {display_type}.")

    def match_screenshot(self, path, feature="APP_STORE_VERSIONS"):
        groups = self.screenshot_groups(feature)
        with Image.open(path) as image:
            image.load()
            width, height = image.size
            alpha = "A" in image.getbands() or "transparency" in image.info
            mime_type = Image.MIME.get(image.format)
        extension, size = os.path.splitext(path)[1].lower(), os.path.getsize(path)
        matched_specs, matched_groups = set(), {}
        for group_id, group in groups.items():
            for spec_id in group["spec_ids"]:
                spec = self.specs[spec_id]
                bounds = spec.get("dimensions") or {}
                values = [bounds.get(key) for key in ("minWidth", "maxWidth", "minHeight", "maxHeight")]
                if (any(value is None for value in values)
                        or not (values[0] <= width <= values[1] and values[2] <= height <= values[3])
                        or extension not in spec.get("fileExtensions", [])
                        or mime_type not in spec.get("mimeTypes", [])
                        or not 0 < size <= spec.get("maxFileSize", 0)
                        or (alpha and not spec.get("alphaAllowed", False))):
                    continue
                matched_specs.add(spec_id)
                matched_groups[group_id] = group["max_count"]
        if not matched_specs:
            raise ValueError(f"{os.path.basename(path)} ({width}×{height}): нет подходящей спецификации скриншота Apple.")
        return {"spec_ids": matched_specs, "groups": matched_groups}


class AssetLibraryUploader:
    def __init__(self, client, logger_callback=None, max_workers=4, progress_callback=None):
        self.client = client
        self.logger = logger_callback or (lambda _message: None)
        self.max_workers = max(1, int(max_workers))
        self.progress = progress_callback or (lambda _done, _total: None)
        self.catalog = None
        self.library_id = None
        self._uploaded = {}
        self._library_image_ids = set()

    def _initialize(self):
        if self.catalog is None:
            self.catalog = ScreenshotAssetCatalog(self.client._request("GET", "appAssetLibraryRefData"))
        if self.library_id is None:
            self.library_id = self.client._request("GET", f"apps/{self.client.app_id}/assetLibrary")["data"]["id"]

    def list_existing_images(self):
        self._initialize()
        images = self._list(f"appAssetLibraries/{self.library_id}/images?limit=200")
        self._library_image_ids = {item["id"] for item in images}
        return images

    def prepare_existing_batches(self, batches, feature="APP_STORE_VERSIONS"):
        if not batches:
            raise ValueError("Выберите изображения из библиотеки и их порядок.")
        self.catalog = None
        self._initialize()
        images = {item["id"]: item for item in self.list_existing_images()}
        return self._validate_existing_batches(batches, feature, images)

    def _validate_existing_batches(self, batches, feature, images):
        if not batches:
            raise ValueError("Выберите изображения из библиотеки и их порядок.")
        groups = self.catalog.screenshot_groups(feature)
        prepared = {}
        for group_id, identifiers in batches.items():
            group = groups.get(group_id)
            if not group or not identifiers or len(identifiers) > group["max_count"]:
                raise ValueError(f"{group_id}: неподдерживаемая группа или превышен лимит Apple (до 10).")
            if len(set(identifiers)) != len(identifiers):
                raise ValueError(f"{group_id}: один ID выбран несколько раз.")
            for identifier in identifiers:
                if identifier not in images:
                    raise ValueError(f"Ассет {identifier} отсутствует в библиотеке этого приложения.")
                self.catalog.validate_existing_image(images[identifier], group)
            prepared[group_id] = {"group": group_id, "assets": list(identifiers)}
        return prepared

    def replace_existing_localization(self, localization_id, batches, relationship="appStoreVersionLocalization",
                                      cleanup_spec_mismatch=False):
        if relationship not in SURFACE_TYPES:
            raise ValueError("Неизвестный тип локализации.")
        feature = "APP_STORE_VERSIONS" if relationship == "appStoreVersionLocalization" else "PRODUCT_PAGE_OPTIMIZATIONS"
        try:
            identifiers = {identifier for batch in batches.values() for identifier in batch}
            if identifiers and identifiers <= self._library_image_ids:
                self.catalog = None
                self._initialize()
                images = {identifier: self.client._request("GET", f"appAssetLibraryImages/{identifier}")["data"]
                          for identifier in identifiers}
                if any(image.get("id") != identifier for identifier, image in images.items()):
                    raise ValueError("Apple вернула другой ID изображения. Изменения отменены.")
                prepared = self._validate_existing_batches(batches, feature, images)
            else:
                prepared = self.prepare_existing_batches(batches, feature)
        except Exception as exc:
            exc.asset_library_retry_safe = True
            raise
        self.replace_localization(localization_id, prepared, relationship)
        if cleanup_spec_mismatch:
            for item in self.list_placements(localization_id, relationship):
                attributes = item.get("attributes") or {}
                if attributes.get("placementGroup") in prepared or not self._is_invalid_spec_mismatch(attributes):
                    continue
                candidate = self.client._request("GET", f"appAssetLibraryPlacements/{item['id']}?include=image")["data"]
                candidate_attributes = candidate.get("attributes") or {}
                image = (((item.get("relationships") or {}).get("image") or {}).get("data") or {}).get("id")
                candidate_image = (((candidate.get("relationships") or {}).get("image") or {}).get("data") or {}).get("id")
                if (candidate.get("id") == item["id"] and image and candidate_image == image
                        and candidate_attributes.get("placementGroup") == attributes.get("placementGroup")
                        and self._is_invalid_spec_mismatch(candidate_attributes)):
                    self._delete_placement(item["id"], localization_id, relationship, candidate_attributes.get("placementGroup"))
                    self.logger(f"Удалено несовместимое размещение {item['id']} (SPEC_MISMATCH); изображение сохранено.")
        current = self.list_placements(localization_id, relationship)
        for batch in prepared.values():
            placements = [item for item in current if (item.get("attributes") or {}).get("placementType") == "APP_SCREENSHOT"
                          and (item.get("attributes") or {}).get("placementGroup") == batch["group"]]
            image_ids = [(((item.get("relationships") or {}).get("image") or {}).get("data") or {}).get("id")
                         for item in placements]
            if image_ids != batch["assets"] or not all((item.get("attributes") or {}).get("state") in READY_PLACEMENT_STATES for item in placements):
                raise RuntimeError("Итоговые ID/порядок/готовность не подтверждены Apple; проверьте локаль вручную.")
        return [f"{item['id']}: {(item.get('attributes') or {})}" for item in current
                if (item.get("attributes") or {}).get("state") in {"INVALID", "FAILED", "REJECTED", "PARENT_INVALID"}]

    @staticmethod
    def _is_invalid_spec_mismatch(attributes):
        if attributes.get("placementType") != "APP_SCREENSHOT" or attributes.get("state") != "INVALID":
            return False
        codes = []
        for key in ("stateDetails", "invalidReasons"):
            if key not in attributes:
                continue
            reasons = attributes[key]
            if not isinstance(reasons, list):
                return False
            for reason in reasons:
                codes.append(reason.get("code") if isinstance(reason, dict) else reason)
        return bool(codes) and all(code == "SPEC_MISMATCH" for code in codes)

    def wait_for_image(self, image_id, spec_ids, max_attempts=60, interval=2):
        for attempt in range(max_attempts):
            resource = self.client._request("GET", f"appAssetLibraryImages/{image_id}")["data"]
            attributes = resource.get("attributes") or {}
            state = attributes.get("state")
            if state in READY_IMAGE_STATES:
                if attributes.get("specId") not in spec_ids:
                    raise ValueError(f"Apple определила другую спецификацию для {attributes.get('fileName', image_id)}.")
                return resource
            if state in {"FAILED", "REJECTED", "ARCHIVED"}:
                raise ValueError(f"Apple: {attributes.get('fileName', image_id)} — {state}: {attributes.get('stateDetails') or 'обработка не выполнена'}")
            if attempt < max_attempts - 1:
                time.sleep(interval)
        raise TimeoutError(f"Apple ещё обрабатывает ассет {image_id}. Старые скрины не изменены; проверьте библиотеку вручную.")

    def upload_image(self, path, spec_ids):
        payload = {"data": {
            "type": "appAssetLibraryImages",
            "attributes": {"fileName": os.path.basename(path), "fileSize": os.path.getsize(path),
                           "category": "APP_SCREENSHOTS_AND_PREVIEWS"},
            "relationships": {"assetLibrary": {"data": {"type": "appAssetLibraries", "id": self.library_id}}},
        }}
        reservation = self.client._request("POST", "appAssetLibraryImages", payload, max_retries=1)["data"]
        image_id = reservation["id"]
        try:
            operations = (reservation.get("attributes") or {}).get("uploadOperations") or []
            if not operations:
                raise ValueError("Apple не вернула операции загрузки изображения.")
            with open(path, "rb") as source:
                for operation in operations:
                    source.seek(operation["offset"])
                    chunk = source.read(operation["length"])
                    if len(chunk) != operation["length"]:
                        raise ValueError("Неверная длина части изображения для отправки в Apple.")
                    headers = {item["name"]: item["value"] for item in operation.get("requestHeaders") or []}
                    for attempt in range(4):
                        try:
                            response = requests.request(operation["method"], operation["url"], headers=headers, data=chunk, timeout=180)
                            response.raise_for_status()
                            break
                        except requests.exceptions.RequestException as exc:
                            retryable = isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.ChunkedEncodingError)) or (
                                exc.response is not None and exc.response.status_code in {429, 500, 502, 503, 504}
                            )
                            if not retryable or attempt == 3:
                                raise RuntimeError("Ошибка передачи файла в Apple; повторите загрузку.") from exc
                            self.logger("⚠️ Сетевая ошибка передачи ассета, повторяю часть файла...")
                            time.sleep(min(2 ** attempt, 8))
            self.client._request("PATCH", f"appAssetLibraryImages/{image_id}", {"data": {
                "type": "appAssetLibraryImages", "id": image_id, "attributes": {"uploaded": True},
            }})
            return self.wait_for_image(image_id, spec_ids)
        except TimeoutError:
            raise
        except Exception:
            try:
                self.client._request("DELETE", f"appAssetLibraryImages/{image_id}")
            except Exception:
                self.logger(f"⚠️ Незавершённый ассет {image_id} остался в библиотеке. Проверьте его вручную.")
            raise

    def validate_batches(self, batches, feature="APP_STORE_VERSIONS"):
        self._initialize()
        validated = {}
        if not batches:
            raise ValueError("Нет наборов скриншотов для загрузки.")
        for display_type, paths in batches.items():
            if not paths:
                raise ValueError("Нет скриншотов для назначения локалям.")
            matches = [self.catalog.match(path, display_type, feature) for path in paths]
            group = matches[0]["group"]
            if any(match["group"] != group for match in matches):
                raise ValueError("Скриншоты одного набора относятся к разным группам Apple.")
            if len(paths) > min(match["max_count"] for match in matches):
                raise ValueError(f"Набор {display_type} превышает лимит скриншотов Apple.")
            validated[display_type] = matches
        return validated

    def prepare_batches(self, batches, feature="APP_STORE_VERSIONS"):
        validated = self.validate_batches(batches, feature)
        plans = {}
        pending = {}
        for display_type, paths in batches.items():
            matches = validated[display_type]
            group = matches[0]["group"]
            digests = []
            for path, match in zip(paths, matches):
                digest = hashlib.sha256()
                with open(path, "rb") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(chunk)
                key = digest.hexdigest()
                digests.append(key)
                if key not in self._uploaded:
                    pending[key] = (path, match["spec_ids"])
                elif self._uploaded[key]["attributes"].get("specId") not in match["spec_ids"]:
                    raise ValueError("Сохранённый ассет не соответствует спецификации выбранной группы.")
            plans[display_type] = {"group": group, "digests": digests}
        self.logger(f"Asset Library: отправляю {len(pending)} уникальных файлов один раз; остальные локали используют эти же ассеты.")
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(self.upload_image, path, specs): key for key, (path, specs) in pending.items()}
            failures = []
            for done, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                try:
                    self._uploaded[futures[future]] = future.result()
                except Exception as exc:
                    failures.append(exc)
                self.progress(done, len(futures))
            if failures:
                if len(failures) == 1:
                    raise failures[0]
                raise RuntimeError("; ".join(str(failure) for failure in failures))
        return {display_type: {"group": plan["group"], "assets": [self._uploaded[key]["id"] for key in plan["digests"]]}
                for display_type, plan in plans.items()}

    def _list(self, endpoint):
        items = []
        visited = set()
        while endpoint:
            if endpoint in visited:
                raise ValueError("Apple вернула повторяющуюся страницу ассетов.")
            visited.add(endpoint)
            response = self.client._request("GET", endpoint)
            items.extend(response.get("data") or [])
            endpoint = (response.get("links") or {}).get("next")
            if endpoint:
                parsed = urlparse(endpoint)
                base = urlparse(self.client.base_url)
                if parsed.scheme != base.scheme or parsed.netloc != base.netloc or not parsed.path.startswith(base.path + "/"):
                    raise ValueError("Apple вернула неизвестный адрес страницы ассетов.")
                endpoint = parsed.path[len(base.path) + 1:] + ("?" + parsed.query if parsed.query else "")
        return items

    def list_placements(self, localization_id, relationship="appStoreVersionLocalization", group=None):
        surface = SURFACE_TYPES[relationship]
        filters = {"filter[placementType]": "APP_SCREENSHOT", "sort": "placementGroupPosition", "include": "image", "limit": 200}
        if group:
            filters["filter[placementGroup]"] = group
        return self._list(f"{surface}/{localization_id}/placements?{urlencode(filters)}")

    def _create_placement(self, localization_id, relationship, group, image_id):
        payload = {"data": {
            "type": "appAssetLibraryPlacements", "attributes": {"placementType": "APP_SCREENSHOT", "placementGroup": group},
            "relationships": {
                "image": {"data": {"type": "appAssetLibraryImages", "id": image_id}},
                relationship: {"data": {"type": SURFACE_TYPES[relationship], "id": localization_id}},
            },
        }}
        return self.client._request("POST", "appAssetLibraryPlacements", payload, max_retries=1)["data"]["id"]

    def _order(self, localization_id, relationship, group, placement_ids):
        if not placement_ids:
            return
        self.client._request("POST", "appAssetLibraryPlacementOrderingRequests", {"data": {
            "type": "appAssetLibraryPlacementOrderingRequests", "attributes": {"placementGroup": group},
            "relationships": {
                "orderedPlacements": {"data": [{"type": "appAssetLibraryPlacements", "id": identifier} for identifier in placement_ids]},
                relationship: {"data": {"type": SURFACE_TYPES[relationship], "id": localization_id}},
            },
        }}, max_retries=1)

    def _verify(self, localization_id, relationship, group, placement_ids):
        for attempt in range(10):
            placements = self.list_placements(localization_id, relationship, group)
            states = [(item.get("attributes") or {}).get("state") for item in placements]
            for item in placements:
                attributes = item.get("attributes") or {}
                if attributes.get("state") in {"FAILED", "INVALID", "REJECTED", "PARENT_INVALID", "PARENT_REJECTED"}:
                    raise ValueError(f"Apple отклонила размещение {item['id']}: {attributes}")
            if [item["id"] for item in placements] == placement_ids and all(state in READY_PLACEMENT_STATES for state in states):
                return
            if attempt < 9:
                time.sleep(1)
        raise TimeoutError("Apple не подтвердила порядок/готовность скриншотов в локали.")

    def _delete_placement(self, identifier, localization_id, relationship, group):
        try:
            self.client._request("DELETE", f"appAssetLibraryPlacements/{identifier}", max_retries=1)
        except Exception:
            for attempt in range(3):
                current = self.list_placements(localization_id, relationship, group)
                if identifier not in {item["id"] for item in current}:
                    self.logger(f"⚠️ Удаление {identifier}: ответ потерян, отсутствие подтверждено чтением Apple.")
                    return
                if attempt < 2:
                    time.sleep(1)
            raise

    def _order_and_verify(self, localization_id, relationship, group, identifiers):
        try:
            self._order(localization_id, relationship, group, identifiers)
        except Exception:
            self._verify(localization_id, relationship, group, identifiers)
            self.logger("⚠️ Ответ назначения порядка не получен; правильный порядок подтверждён чтением Apple.")
        else:
            self._verify(localization_id, relationship, group, identifiers)

    def _reconcile_creation(self, localization_id, relationship, group, image_id, known_ids):
        for attempt in range(5):
            placements = self.list_placements(localization_id, relationship, group)
            candidates = {
                item["id"] for item in placements
                if item["id"] not in known_ids
                and (((item.get("relationships") or {}).get("image") or {}).get("data") or {}).get("id") == image_id
            }
            if candidates:
                return candidates
            if attempt < 4:
                time.sleep(1)
        return None

    def _restore_localization(self, localization_id, relationship, changed, ambiguous, known_ids):
        unresolved = False
        for snapshot in reversed(changed):
            group = snapshot["group"]
            display_type = snapshot["display_type"]
            try:
                observed = set()
                if group in ambiguous:
                    candidates = self._reconcile_creation(
                        localization_id, relationship, group, ambiguous[group], known_ids
                    )
                    if candidates is None:
                        unresolved = True
                    else:
                        observed.update(candidates)
                current = self.list_placements(localization_id, relationship, group)
                observed.update(snapshot["created"])
                observed.update(item["id"] for item in current
                                if (item.get("attributes") or {}).get("placementType") == "APP_SCREENSHOT")
                for identifier in observed:
                    self._delete_placement(identifier, localization_id, relationship, group)
                restored = []
                for image_id in snapshot["images"]:
                    try:
                        identifier = self._create_placement(localization_id, relationship, group, image_id)
                    except Exception:
                        candidates = self._reconcile_creation(localization_id, relationship, group, image_id, known_ids)
                        if not candidates or len(candidates) != 1:
                            raise RuntimeError("Ответ восстановления потерян; результат не подтверждён, проверьте локаль вручную.")
                        identifier = next(iter(candidates))
                        self.logger(f"Восстановление {identifier}: ответ потерян, размещение подтверждено чтением Apple.")
                    known_ids.add(identifier)
                    restored.append(identifier)
                self._order_and_verify(localization_id, relationship, group, restored)
            except Exception as restore_error:
                unresolved = True
                self.logger(
                    f"⚠️ {display_type}: не удалось подтвердить восстановление: {restore_error}. "
                    "Проверьте локаль вручную; общие ассеты остались в библиотеке."
                )
        if unresolved:
            self.logger(
                "⚠️ Исход создания/восстановления размещений не подтверждён окончательно. "
                "Возможны отложенные изменения Apple: проверьте локаль вручную."
            )
        else:
            self.logger("⚠️ Восстановил предыдущие скрины всех изменённых групп; порядок проверен.")

        return not unresolved

    def replace_localization(self, localization_id, prepared, relationship="appStoreVersionLocalization"):
        try:
            existing = self.list_placements(localization_id, relationship)
        except Exception as exc:
            exc.asset_library_retry_safe = True
            raise
        snapshots = []
        for display_type, batch in prepared.items():
            group = batch["group"]
            previous = [item for item in existing if (item.get("attributes") or {}).get("placementType") == "APP_SCREENSHOT"
                        and (item.get("attributes") or {}).get("placementGroup") == group]
            images = []
            for item in previous:
                image = ((item.get("relationships") or {}).get("image") or {}).get("data") or {}
                if not image.get("id"):
                    raise ValueError("Apple не вернула ссылки старых ассетов. Замена отменена, старые скрины сохранены.")
                images.append(image["id"])
            snapshots.append({"display_type": display_type, "group": group, "previous": previous,
                              "images": images, "new_images": batch["assets"], "created": []})
        changed = []
        ambiguous = {}
        known_ids = {item["id"] for item in existing}
        try:
            for snapshot in snapshots:
                group = snapshot["group"]
                changed.append(snapshot)
                for item in snapshot["previous"]:
                    self._delete_placement(item["id"], localization_id, relationship, group)
                new_placements = []
                for image_id in snapshot["new_images"]:
                    try:
                        identifier = self._create_placement(localization_id, relationship, group, image_id)
                    except Exception:
                        ambiguous[group] = image_id
                        raise
                    known_ids.add(identifier)
                    snapshot["created"].append(identifier)
                    new_placements.append(identifier)
                self._order_and_verify(localization_id, relationship, group, new_placements)
            for snapshot in snapshots:
                self.logger(
                    f"✅ {snapshot['display_type']}: назначено {len(snapshot['new_images'])} скринов "
                    "из библиотеки, порядок проверен."
                )
        except Exception as exc:
            exc.asset_library_retry_safe = self._restore_localization(
                localization_id, relationship, changed, ambiguous, known_ids
            )
            raise
