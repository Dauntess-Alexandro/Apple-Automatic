"""Локальная сборка IPA + заливка в App Store Connect (замена Codemagic)."""

from __future__ import annotations

import glob
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Callable

Logger = Callable[[str], None]

BULDER_SCRIPT_NAME = "bulder_pushwoosh_build_config.py"
LEAK_PATTERNS = [
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "IP-адрес"),
    (re.compile(r"/Users/[A-Za-z0-9._-]+"), "путь /Users/..."),
    (re.compile(r"\b([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b"), "MAC-адрес"),
]
SCAN_EXTENSIONS = {".swift", ".m", ".h", ".mm", ".plist", ".json", ".txt", ".md", ".py", ".yml", ".yaml"}


def _run(cmd, cwd=None, env=None, log: Logger | None = None) -> str:
    if log:
        log("$ " + " ".join(cmd))
    completed = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    out = (completed.stdout or "") + (("\n" + completed.stderr) if completed.stderr else "")
    if completed.returncode != 0:
        tail = out.strip()[-4000:] if out.strip() else "(no output)"
        raise RuntimeError(f"Команда завершилась с кодом {completed.returncode}:\n{tail}")
    return out


def find_xcodeproj(project_dir: str) -> str:
    project_dir = os.path.abspath(os.path.expanduser(project_dir))
    if project_dir.endswith(".xcodeproj") and os.path.isdir(project_dir):
        return project_dir
    matches = sorted(glob.glob(os.path.join(project_dir, "*.xcodeproj")))
    if not matches:
        raise FileNotFoundError(f"В папке нет .xcodeproj: {project_dir}")
    return matches[0]


def normalize_project_name_key(name: str) -> str:
    """River Aspect / River-Aspect / RiverAspect → riveraspect."""
    return re.sub(r"[^a-z0-9]+", "", (name or "").strip().lower())


def find_xcode_project_dir_by_app_name(
    app_name: str,
    projects_root: str,
    bundle_id: str = "",
) -> str | None:
    """Ищет папку с .xcodeproj в projects_root по имени приложения (как GitLab)."""
    root = os.path.abspath(os.path.expanduser(projects_root or ""))
    if not root or not os.path.isdir(root):
        return None

    name_key = normalize_project_name_key(app_name)
    bundle_key = ""
    full_bundle = ""
    if bundle_id:
        # com.josip.vmvsl → vmvsl; spillwheel.watermill → spillwheelwatermill / spillwheel
        tail = bundle_id.strip().split(".")[-1]
        bundle_key = normalize_project_name_key(tail)
        full_bundle = normalize_project_name_key(bundle_id)

    candidates: list[tuple[int, str]] = []
    try:
        entries = os.listdir(root)
    except OSError:
        return None

    for entry in entries:
        path = os.path.join(root, entry)
        if not os.path.isdir(path) or entry.startswith("."):
            continue
        try:
            find_xcodeproj(path)
        except FileNotFoundError:
            continue

        folder_key = normalize_project_name_key(entry)
        score = 0
        if name_key and folder_key == name_key:
            score = 100
        elif name_key and (name_key in folder_key or folder_key in name_key):
            score = 80
        elif bundle_key and folder_key == bundle_key:
            score = 70
        elif bundle_key and (bundle_key in folder_key or folder_key in bundle_key):
            score = 60
        elif full_bundle and folder_key and folder_key in full_bundle:
            score = 50
        if score:
            candidates.append((score, path))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1].lower()))
    return candidates[0][1]


def discover_scheme(project_path: str, preferred: str = "") -> str:
    preferred = (preferred or "").strip()
    if preferred:
        return preferred
    out = _run(["xcodebuild", "-list", "-project", project_path])
    schemes: list[str] = []
    in_schemes = False
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("Schemes:"):
            in_schemes = True
            continue
        if in_schemes:
            if not stripped:
                break
            if stripped.endswith(":"):
                break
            schemes.append(stripped)
    if not schemes:
        base = os.path.splitext(os.path.basename(project_path))[0]
        return base
    base = os.path.splitext(os.path.basename(project_path))[0]
    if base in schemes:
        return base
    return schemes[0]


def find_bulder_script(project_dir: str) -> str | None:
    candidate = os.path.join(project_dir, "Scripts", BULDER_SCRIPT_NAME)
    return candidate if os.path.isfile(candidate) else None


def materialize_bulder_config(project_dir: str, log: Logger) -> dict[str, str]:
    script = find_bulder_script(project_dir)
    if not script:
        log("Bulder/Pushwoosh скрипт не найден — пропускаю.")
        return {}
    log(f"Запуск {os.path.relpath(script, project_dir)}...")
    env = os.environ.copy()
    env["CM_BUILD_DIR"] = project_dir
    _run(["python3", script], cwd=project_dir, env=env, log=log)
    env_file = os.path.join(project_dir, ".bulder_pushwoosh", "pushwoosh.env")
    values: dict[str, str] = {}
    if os.path.isfile(env_file):
        with open(env_file, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or "=" not in line or line.startswith("#"):
                    continue
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
        log(f"Bulder config: {len(values)} переменных из pushwoosh.env")
    return values


def scan_project_leaks(project_dir: str, log: Logger) -> list[str]:
    findings: list[str] = []
    roots = [
        os.path.join(project_dir, "Sources"),
        os.path.join(project_dir, "Resources"),
        os.path.join(project_dir, "Scripts"),
    ]
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _, filenames in os.walk(root):
            if ".bulder_pushwoosh" in dirpath.split(os.sep):
                continue
            for name in filenames:
                ext = os.path.splitext(name)[1].lower()
                if ext not in SCAN_EXTENSIONS:
                    continue
                path = os.path.join(dirpath, name)
                try:
                    text = open(path, encoding="utf-8", errors="ignore").read()
                except OSError:
                    continue
                for pattern, label in LEAK_PATTERNS:
                    for match in pattern.finditer(text):
                        value = match.group(0)
                        if label == "IP-адрес" and value.startswith("127."):
                            continue
                        rel = os.path.relpath(path, project_dir)
                        findings.append(f"{label}: {value} → {rel}")
    if findings:
        log(f"⚠ Найдено возможных утечек: {len(findings)}")
        for item in findings[:40]:
            log(f"  • {item}")
        if len(findings) > 40:
            log(f"  … и ещё {len(findings) - 40}")
    else:
        log("Проверка утечек: явных IP /Users / MAC не найдено.")
    return findings


def fetch_latest_build_number(asc_client) -> int:
    """Максимальный известный build number из ASC builds."""
    endpoint = f"builds?filter[app]={asc_client.app_id}&sort=-uploadedDate&limit=50"
    data = asc_client._request("GET", endpoint)
    best = 0
    for item in data.get("data") or []:
        raw = str((item.get("attributes") or {}).get("version") or "").strip()
        if raw.isdigit():
            best = max(best, int(raw))
    return best


def bump_project_build_number(project_dir: str, build_number: int, log: Logger) -> None:
    log(f"agvtool: CURRENT_PROJECT_VERSION → {build_number}")
    _run(["xcrun", "agvtool", "new-version", "-all", str(build_number)], cwd=project_dir, log=log)


def ensure_altool_api_key(key_id: str, private_key_path: str, log: Logger) -> None:
    key_id = (key_id or "").strip()
    private_key_path = os.path.abspath(os.path.expanduser(private_key_path or ""))
    if not key_id:
        raise ValueError("Нет KEY_ID для altool.")
    if not os.path.isfile(private_key_path):
        raise FileNotFoundError(f"Нет .p8: {private_key_path}")
    dest_dir = os.path.join(os.path.expanduser("~"), ".appstoreconnect", "private_keys")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"AuthKey_{key_id}.p8")
    if os.path.isfile(dest):
        try:
            if os.path.samefile(dest, private_key_path):
                return
        except OSError:
            pass
    shutil.copy2(private_key_path, dest)
    os.chmod(dest, 0o600)
    log(f"altool: ключ скопирован → {dest}")


def write_export_options(path: str, team_id: str) -> None:
    options = {
        "method": "app-store-connect",
        "destination": "export",
        "signingStyle": "automatic",
        "uploadSymbols": True,
        "manageAppVersionAndBuildNumber": False,
    }
    if team_id:
        options["teamID"] = team_id
    with open(path, "wb") as handle:
        plistlib.dump(options, handle)


def archive_and_export_ipa(
    project_dir: str,
    scheme: str,
    team_id: str,
    output_dir: str,
    log: Logger,
) -> str:
    project_path = find_xcodeproj(project_dir)
    scheme = discover_scheme(project_path, scheme)
    os.makedirs(output_dir, exist_ok=True)
    archive_path = os.path.join(output_dir, f"{scheme}.xcarchive")
    export_dir = os.path.join(output_dir, "ipa")
    if os.path.isdir(archive_path):
        shutil.rmtree(archive_path)
    if os.path.isdir(export_dir):
        shutil.rmtree(export_dir)
    os.makedirs(export_dir, exist_ok=True)

    archive_cmd = [
        "xcodebuild",
        "archive",
        "-project", project_path,
        "-scheme", scheme,
        "-configuration", "Release",
        "-archivePath", archive_path,
        "-destination", "generic/platform=iOS",
        "-allowProvisioningUpdates",
        "CODE_SIGN_STYLE=Automatic",
    ]
    if team_id:
        archive_cmd.append(f"DEVELOPMENT_TEAM={team_id}")

    log("xcodebuild archive… (может занять несколько минут)")
    _run(archive_cmd, cwd=project_dir, log=log)

    export_plist = os.path.join(output_dir, "ExportOptions.plist")
    write_export_options(export_plist, team_id)
    export_cmd = [
        "xcodebuild",
        "-exportArchive",
        "-archivePath", archive_path,
        "-exportPath", export_dir,
        "-exportOptionsPlist", export_plist,
        "-allowProvisioningUpdates",
    ]
    log("xcodebuild -exportArchive…")
    _run(export_cmd, cwd=project_dir, log=log)

    ipas = sorted(glob.glob(os.path.join(export_dir, "*.ipa")))
    if not ipas:
        raise FileNotFoundError(f"IPA не найден в {export_dir}")
    log(f"IPA готов: {ipas[0]}")
    return ipas[0]


def upload_ipa_altool(
    ipa_path: str,
    key_id: str,
    issuer_id: str,
    private_key_path: str,
    log: Logger,
) -> None:
    ensure_altool_api_key(key_id, private_key_path, log)
    log(f"altool upload: {os.path.basename(ipa_path)}")
    _run(
        [
            "xcrun", "altool",
            "--upload-app",
            "-f", ipa_path,
            "-t", "ios",
            "--apiKey", key_id,
            "--apiIssuer", issuer_id,
        ],
        log=log,
    )
    log("Загрузка IPA в App Store Connect завершена.")


def build_and_optionally_upload(
    *,
    project_dir: str,
    scheme: str,
    team_id: str,
    apple_id: str,
    issuer_id: str,
    key_id: str,
    private_key_path: str,
    bump_build: bool,
    upload: bool,
    scan_leaks: bool,
    run_bulder: bool,
    asc_client=None,
    log: Logger = print,
) -> dict:
    if sys_platform_not_mac():
        raise RuntimeError("Сборка IPA доступна только на macOS с Xcode.")

    project_dir = os.path.abspath(os.path.expanduser(project_dir))
    if not os.path.isdir(project_dir):
        raise FileNotFoundError(f"Нет папки проекта: {project_dir}")

    project_path = find_xcodeproj(project_dir)
    scheme = discover_scheme(project_path, scheme)
    log(f"Проект: {project_path}")
    log(f"Scheme: {scheme}")

    bulder_env: dict[str, str] = {}
    if run_bulder:
        bulder_env = materialize_bulder_config(project_dir, log)
        team_id = (
            (team_id or "").strip()
            or bulder_env.get("DEVELOPMENT_TEAM", "").strip()
            or bulder_env.get("APPLE_TEAM_ID", "").strip()
        )

    if scan_leaks:
        scan_project_leaks(project_dir, log)

    if bump_build:
        if asc_client is None:
            raise ValueError("Для +1 build number нужен ASC API (Issuer/Key/.p8/App ID).")
        latest = fetch_latest_build_number(asc_client)
        next_build = latest + 1
        log(f"Последний build в ASC: {latest} → ставим {next_build}")
        bump_project_build_number(project_dir, next_build, log)
    else:
        next_build = None

    if not team_id:
        log("⚠ DEVELOPMENT_TEAM пустой — Xcode попробует automatic signing из локальных аккаунтов.")

    output_dir = tempfile.mkdtemp(prefix="ppo_ipa_")
    try:
        ipa_path = archive_and_export_ipa(
            project_dir=project_dir,
            scheme=scheme,
            team_id=team_id,
            output_dir=output_dir,
            log=log,
        )
        stable_dir = os.path.join(project_dir, "build", "ppo_ipa")
        os.makedirs(stable_dir, exist_ok=True)
        stable_ipa = os.path.join(stable_dir, os.path.basename(ipa_path))
        shutil.copy2(ipa_path, stable_ipa)
        log(f"Копия IPA: {stable_ipa}")

        if upload:
            if not (issuer_id and key_id and private_key_path):
                raise ValueError("Для заливки IPA нужны Issuer ID, Key ID и .p8 (вкладка Настройки API).")
            upload_ipa_altool(
                ipa_path=stable_ipa,
                key_id=key_id,
                issuer_id=issuer_id,
                private_key_path=private_key_path,
                log=log,
            )

        return {
            "ipa_path": stable_ipa,
            "scheme": scheme,
            "team_id": team_id,
            "build_number": next_build,
            "uploaded": bool(upload),
            "apple_id": apple_id,
        }
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


def sys_platform_not_mac() -> bool:
    return sys.platform != "darwin"
